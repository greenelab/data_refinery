from datetime import datetime, timedelta
from re import match

from django.db.models import Count, DateTimeField, OuterRef, Prefetch, Subquery
from django.db.models.aggregates import Avg, Sum
from django.db.models.expressions import F, Q
from django.db.models.functions import Left, Trunc
from django.http import JsonResponse, QueryDict
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from rest_framework import filters, generics, status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from django_elasticsearch_dsl_drf.constants import (
    LOOKUP_FILTER_RANGE,
    LOOKUP_QUERY_GT,
    LOOKUP_QUERY_IN,
)
from django_elasticsearch_dsl_drf.filter_backends import (
    CompoundSearchFilterBackend,
    DefaultOrderingFilterBackend,
    FacetedSearchFilterBackend,
    FilteringFilterBackend,
    OrderingFilterBackend,
)
from django_elasticsearch_dsl_drf.pagination import LimitOffsetPagination as ESLimitOffsetPagination
from django_elasticsearch_dsl_drf.viewsets import DocumentViewSet
from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from elasticsearch_dsl import TermsFacet
from six import iteritems

from data_refinery_common.job_lookup import ProcessorPipeline
from data_refinery_common.logging import get_and_configure_logger
from data_refinery_common.message_queue import send_job
from data_refinery_common.models import (
    APIToken,
    CompendiumResult,
    ComputationalResult,
    ComputationalResultAnnotation,
    ComputedFile,
    Dataset,
    DatasetAnnotation,
    DownloaderJob,
    Experiment,
    Organism,
    OrganismIndex,
    OriginalFile,
    Processor,
    ProcessorJob,
    ProcessorJobDatasetAssociation,
    Sample,
    SurveyJob,
)
from data_refinery_common.models.documents import ExperimentDocument
from data_refinery_common.utils import get_active_volumes, get_nomad_jobs, get_nomad_jobs_breakdown

from .serializers import ExperimentDocumentSerializer

from data_refinery_api.serializers import (  # Job; Dataset
    APITokenSerializer,
    CompendiumResultSerializer,
    CompendiumResultWithUrlSerializer,
    ComputationalResultSerializer,
    ComputationalResultWithUrlSerializer,
    ComputedFileListSerializer,
    CreateDatasetSerializer,
    DatasetSerializer,
    DetailedExperimentSerializer,
    DetailedSampleSerializer,
    DownloaderJobSerializer,
    ExperimentSerializer,
    InstitutionSerializer,
    OrganismIndexSerializer,
    OrganismSerializer,
    OriginalFileListSerializer,
    PlatformSerializer,
    ProcessorJobSerializer,
    ProcessorSerializer,
    QNTargetSerializer,
    SurveyJobSerializer,
)

logger = get_and_configure_logger(__name__)

##
# Variables
##

JOB_CREATED_AT_CUTOFF = datetime(2019, 6, 5, tzinfo=timezone.utc)


class FacetedSearchFilterBackendExtended(FacetedSearchFilterBackend):
    def aggregate(self, request, queryset, view):
        """Extends FacetedSearchFilterBackend to add additional metrics to each bucket
        https://github.com/barseghyanartur/django-elasticsearch-dsl-drf/blob/master/src/django_elasticsearch_dsl_drf/filter_backends/faceted_search.py#L19

        We have the downloadable sample accession codes indexed for each experiment.
        The cardinality metric, returns the number of unique samples for each bucket.
        However it's just an approximate
        https://www.elastic.co/guide/en/elasticsearch/reference/current/search-aggregations-metrics-cardinality-aggregation.html#_counts_are_approximate
        I used the highest possible precision threshold, but this might increase the amount
        of memory used.
        """
        facets = self.construct_facets(request, view)
        for field, facet in iteritems(facets):
            agg = facet["facet"].get_aggregation()
            queryset.aggs.bucket(field, agg).metric(
                "total_samples",
                "cardinality",
                field="downloadable_samples",
                precision_threshold=40000,
            )
        return queryset


class POSTFilteringFilterBackend(FilteringFilterBackend):
    """Adapts FilteringFilterBackend to take queries from POST requests"""

    class MockRequest:
        """A mock request object to give to FilteringFilterBackend that
        only has a query_params field.

        The purpose of this class is to convert the request data to a form
        that can be understood by FilteringFilterBackend
        """

        def add_to_params(self, key, value):
            """Add to query params, converting to string if necessary"""
            if type(value) == str:
                self.query_params.appendlist(key, value)
            elif type(value) == int or type(value) == bool:
                self.query_params.appendlist(key, str(value))
            else:
                # We shouldn't be filtering on Null, lists, or dicts
                raise serializers.ValidationError(
                    "Invalid type {} for filter value {}".format(type(value), str(value))
                )

        def __init__(self, request):
            self.query_params = QueryDict(mutable=True)

            for key, value in request.data.items():
                if type(value) == list:
                    for item in value:
                        self.add_to_params(key, item)
                else:
                    self.add_to_params(key, value)

    def get_filter_query_params(self, request, view):
        """Override get_filter_query_params to insert our own in POST requests."""
        if request.method != "POST":
            return {}

        return super(POSTFilteringFilterBackend, self).get_filter_query_params(
            POSTFilteringFilterBackend.MockRequest(request), view
        )

    def get_schema_fields(self, view):
        """Return no schema fields since we don't use any query parameters.

        This has to be defined, otherwise we get FilteringFilterBackend's
        schema fields instead, which causes an error if both of them are used
        in the same view."""
        return []


class FormlessBrowsableAPIRenderer(BrowsableAPIRenderer):
    """A BrowsableAPIRenderer that never tries to display a form for any
    method.

    We use this in ExperimentDocumentView because otherwise we get an error
    when trying to generate the form for POST requests.
    """

    def show_form_for_method(self, view, method, request, instance):
        return False


##
# ElasticSearch powered Search and Filter
##
@method_decorator(
    name="list",
    decorator=swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="accession_code",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Allows filtering the results by accession code, can have multiple values. Eg: `?accession_code=microarray&accession_code=rna-seq`",
            ),
            openapi.Parameter(
                name="technology",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Allows filtering the results by technology, can have multiple values. Eg: `?technology=microarray&technology=rna-seq`",
            ),
            openapi.Parameter(
                name="has_publication",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Filter the results that have associated publications with `?has_publication=true`",
            ),
            openapi.Parameter(
                name="platform",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Allows filtering the results by platform, this parameter can have multiple values.",
            ),
            openapi.Parameter(
                name="organism",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Allows filtering the results by organism, this parameter can have multiple values.",
            ),
            openapi.Parameter(
                name="num_processed_samples",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_NUMBER,
                description="Use ElasticSearch queries to specify the number of processed samples of the results",
            ),
        ],
        operation_description="""
Use this endpoint to search among the experiments.

This is powered by ElasticSearch, information regarding advanced usages of the
filters can be found in the [Django-ES-DSL-DRF docs](https://django-elasticsearch-dsl-drf.readthedocs.io/en/0.17.1/filtering_usage_examples.html#filtering)

There's an additional field in the response named `facets` that contain stats on the number of results per filter type.

Example Requests:
```
?search=medulloblastoma
?id=1
?search=medulloblastoma&technology=microarray&has_publication=true
?ordering=source_first_published
```

This endpoint also accepts POST requests for larger queries. Any of the filters
accepted as query parameters are also accepted in a JSON object in the request
body.

Example Requests (from our tests):
```python
import requests
import json

headers = {
    'Content-Type': 'application/json',
}

# Basic filter
search = {"accession_code": "GSE123"}
requests.post(host + '/v1/search/', json.dumps(search), headers=headers)

# __in filter
search = {"accession_code__in": ["GSE123"]}
requests.post(host + '/v1/search/', json.dumps(search), headers=headers)

# numeric filter
search = {"num_downloadable_samples__gt": 0}
requests.post(host + '/v1/search/', json.dumps(search), headers=headers)
```
""",
    ),
)
class ExperimentDocumentView(DocumentViewSet):
    """ ElasticSearch powered experiment search. """

    document = ExperimentDocument
    serializer_class = ExperimentDocumentSerializer
    pagination_class = ESLimitOffsetPagination
    renderer_classes = [JSONRenderer, FormlessBrowsableAPIRenderer]

    # Filter backends provide different functionality we want
    filter_backends = [
        FilteringFilterBackend,
        POSTFilteringFilterBackend,
        OrderingFilterBackend,
        DefaultOrderingFilterBackend,
        CompoundSearchFilterBackend,
        FacetedSearchFilterBackendExtended,
    ]

    # Primitive
    lookup_field = "id"

    # Define search fields
    # Is this exhaustive enough?
    search_fields = {
        "title": {"boost": 10},
        "publication_authors": {"boost": 8},  # "People will search themselves"
        "publication_title": {"boost": 5},
        "submitter_institution": {"boost": 3},
        "description": {"boost": 2},
        "accession_code": None,
        "alternate_accession_code": None,
        "publication_doi": None,
        "pubmed_id": None,
        "sample_metadata_fields": None,
        "platform_names": None,
    }

    # Define filtering fields
    filter_fields = {
        "id": {"field": "_id", "lookups": [LOOKUP_FILTER_RANGE, LOOKUP_QUERY_IN],},
        "technology": "technology",
        "has_publication": "has_publication",
        "accession_code": "accession_code",
        "alternate_accession_code": "alternate_accession_code",
        "platform": "platform_accession_codes",
        "organism": "organism_names",
        "num_processed_samples": {
            "field": "num_processed_samples",
            "lookups": [LOOKUP_FILTER_RANGE, LOOKUP_QUERY_IN, LOOKUP_QUERY_GT],
        },
        "num_downloadable_samples": {
            "field": "num_downloadable_samples",
            "lookups": [LOOKUP_FILTER_RANGE, LOOKUP_QUERY_IN, LOOKUP_QUERY_GT],
        },
    }

    # Define ordering fields
    ordering_fields = {
        "id": "id",
        "title": "title.raw",
        "description": "description.raw",
        "num_total_samples": "num_total_samples",
        "num_downloadable_samples": "num_downloadable_samples",
        "source_first_published": "source_first_published",
    }

    # Specify default ordering
    ordering = (
        "_score",
        "-num_total_samples",
        "id",
        "title",
        "description",
        "-source_first_published",
    )

    # Facets (aka Aggregations) provide statistics about the query result set in the API response.
    # More information here: https://github.com/barseghyanartur/django-elasticsearch-dsl-drf/blob/03a3aa716db31868ca3a71340513a993741a4177/src/django_elasticsearch_dsl_drf/filter_backends/faceted_search.py#L24
    faceted_search_fields = {
        "technology": {
            "field": "technology",
            "facet": TermsFacet,
            "enabled": True,  # These are enabled by default, which is more expensive but more simple.
        },
        "organism_names": {
            "field": "organism_names.raw",
            "facet": TermsFacet,
            "enabled": True,
            "options": {"size": 999999},
        },
        "platform_accession_codes": {
            "field": "platform_accession_codes",
            "facet": TermsFacet,
            "enabled": True,
            "global": False,
            "options": {"size": 999999},
        },
        "has_publication": {
            "field": "has_publication",
            "facet": TermsFacet,
            "enabled": True,
            "global": False,
        },
        # We don't actually need any "globals" to drive our web frontend,
        # but we'll leave them available but not enabled by default, as they're
        # expensive.
        "technology_global": {
            "field": "technology",
            "facet": TermsFacet,
            "enabled": False,
            "global": True,
        },
        "organism_names_global": {
            "field": "organism_names",
            "facet": TermsFacet,
            "enabled": False,
            "global": True,
            "options": {"size": 999999},
        },
        "platform_names_global": {
            "field": "platform_names",
            "facet": TermsFacet,
            "enabled": False,
            "global": True,
            "options": {"size": 999999},
        },
        "has_publication_global": {
            "field": "platform_names",
            "facet": TermsFacet,
            "enabled": False,
            "global": True,
        },
    }
    faceted_search_param = "facet"

    # Define a separate post method so that we can hide it in the
    # documentation. Otherwise, the auto-generated documentation for the post
    # method is incorrect
    @swagger_auto_schema(auto_schema=None)
    def post(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        response = super(ExperimentDocumentView, self).list(request, args, kwargs)
        response.data["facets"] = self.transform_es_facets(response.data["facets"])
        return response

    def transform_es_facets(self, facets):
        """Transforms Elastic Search facets into a set of objects where each one corresponds
        to a filter group. Example:

        { technology: {rna-seq: 254, microarray: 8846, unknown: 0} }

        Which means the users could attach `?technology=rna-seq` to the url and expect 254
        samples returned in the results.
        """
        result = {}
        for field, facet in iteritems(facets):
            filter_group = {}
            for bucket in facet["buckets"]:
                if field == "has_publication":
                    filter_group[bucket["key_as_string"]] = bucket["total_samples"]["value"]
                else:
                    filter_group[bucket["key"]] = bucket["total_samples"]["value"]
            result[field] = filter_group
        return result


##
# Dataset
##


class CreateDatasetView(generics.CreateAPIView):
    """ Creates and returns new Datasets. """

    queryset = Dataset.objects.all()
    serializer_class = CreateDatasetSerializer


@method_decorator(
    name="get",
    decorator=swagger_auto_schema(
        operation_description="View a single Dataset.",
        manual_parameters=[
            openapi.Parameter(
                name="details",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_BOOLEAN,
                description="When set to `True`, additional fields will be included in the response with details about the experiments in the dataset. This is used mostly on the dataset page in www.refine.bio",
            )
        ],
    ),
)
@method_decorator(
    name="patch", decorator=swagger_auto_schema(auto_schema=None)
)  # partial updates not supported
@method_decorator(
    name="put",
    decorator=swagger_auto_schema(
        operation_description="""
Modify an existing Dataset.

In order to begin smashing, an activated API key must be provided in the `API-KEY` header field of the request.
To acquire and activate an API key see the documentation for the [/token](#tag/token)
endpoint.

```py
import requests
import json

params = json.dumps({
    'data': data,
    'aggregate_by': 'EXPERIMENT',
    'start': True,
    'email_address': 'refinebio@gmail.com'
})
headers = {
    'Content-Type': 'application/json',
    'API-KEY': token_id # requested from /token
}
requests.put(host + '/v1/dataset/38879729-93c8-436d-9293-b95d3f274741/', params, headers=headers)
```
"""
    ),
)
class DatasetView(generics.RetrieveUpdateAPIView):
    """ View and modify a single Dataset. """

    queryset = Dataset.objects.all()
    serializer_class = DatasetSerializer
    lookup_field = "id"

    def get_serializer_context(self):
        """
        Extra context provided to the serializer class.
        """
        serializer_context = super(DatasetView, self).get_serializer_context()
        token_id = self.request.META.get("HTTP_API_KEY", None)
        try:
            token = APIToken.objects.get(id=token_id, is_activated=True)
            return {**serializer_context, "token": token}
        except Exception:  # General APIToken.DoesNotExist or django.core.exceptions.ValidationError
            return serializer_context

    def perform_update(self, serializer):
        """ If `start` is set, fire off the job. Disables dataset data updates after that. """
        old_object = self.get_object()
        old_data = old_object.data
        old_aggregate = old_object.aggregate_by
        already_processing = old_object.is_processing
        new_data = serializer.validated_data

        qn_organisms = Organism.get_objects_with_qn_targets()

        # We convert 'ALL' into the actual accession codes given
        for key in new_data["data"].keys():
            accessions = new_data["data"][key]
            if accessions == ["ALL"]:
                experiment = get_object_or_404(Experiment, accession_code=key)

                sample_codes = list(
                    experiment.samples.filter(
                        is_processed=True, organism__in=qn_organisms
                    ).values_list("accession_code", flat=True)
                )
                new_data["data"][key] = sample_codes

        if old_object.is_processed:
            raise APIException("You may not update Datasets which have already been processed")

        if new_data.get("start"):

            # Make sure we have a valid activated token.
            token_id = self.request.data.get("token_id", None)

            if not token_id:
                token_id = self.request.META.get("HTTP_API_KEY", None)

            try:
                token = APIToken.objects.get(id=token_id, is_activated=True)
            except Exception:  # General APIToken.DoesNotExist or django.core.exceptions.ValidationError
                raise APIException("You must provide an active API token ID")

            supplied_email_address = self.request.data.get("email_address", None)
            email_ccdl_ok = self.request.data.get("email_ccdl_ok", False)

            if not already_processing:
                # Create and dispatch the new job.
                processor_job = ProcessorJob()
                processor_job.pipeline_applied = "SMASHER"
                processor_job.ram_amount = 4096
                processor_job.save()

                pjda = ProcessorJobDatasetAssociation()
                pjda.processor_job = processor_job
                pjda.dataset = old_object
                pjda.save()

                job_sent = False

                obj = serializer.save()
                if supplied_email_address is not None:
                    if obj.email_address != supplied_email_address:
                        obj.email_address = supplied_email_address
                        obj.save()
                if email_ccdl_ok:
                    obj.email_ccdl_ok = email_ccdl_ok
                    obj.save()

                try:
                    # Hidden method of non-dispatching for testing purposes.
                    if not self.request.data.get("no_send_job", False):
                        job_sent = send_job(ProcessorPipeline.SMASHER, processor_job)
                    else:
                        # We didn't actually send it, but we also didn't want to.
                        job_sent = True
                except Exception:
                    # job_sent is already false and the exception has
                    # already been logged by send_job, so nothing to
                    # do other than catch the exception.
                    pass

                if not job_sent:
                    raise APIException(
                        "Unable to queue download job. Something has gone"
                        " wrong and we have been notified about it."
                    )

                serializer.validated_data["is_processing"] = True
                obj = serializer.save()

                # create a new dataset annotation with the information of this request
                annotation = DatasetAnnotation()
                annotation.dataset = old_object
                annotation.data = {
                    "start": True,
                    "ip": get_client_ip(self.request),
                    "user_agent": self.request.META.get("HTTP_USER_AGENT", None),
                }
                annotation.save()

                return obj

        # Don't allow critical data updates to jobs that have already been submitted,
        # but do allow email address updating.
        if already_processing:
            serializer.validated_data["data"] = old_data
            serializer.validated_data["aggregate_by"] = old_aggregate
        serializer.save()


class CreateApiTokenView(generics.CreateAPIView):
    """
    token_create

    This endpoint can be used to create and activate tokens. These tokens can be used
    in requests that provide urls to download computed files. They are a way to accept
    our terms of service.

    ```py
    import requests
    import json
    ​
    response = requests.post('https://api.refine.bio/v1/token/')
    token_id = response.json()['id']
    response = requests.put('https://api.refine.bio/v1/token/' + token_id + '/', json.dumps({'is_activated': True}), headers={'Content-Type': 'application/json'})
    ```

    The token id needs to be provided in the HTTP request in the API-KEY header.

    References
    - [https://github.com/AlexsLemonade/refinebio/issues/731]()
    - [https://github.com/AlexsLemonade/refinebio-frontend/issues/560]()
    """

    model = APIToken
    serializer_class = APITokenSerializer


@method_decorator(name="patch", decorator=swagger_auto_schema(auto_schema=None))
class APITokenView(generics.RetrieveUpdateAPIView):
    """
    Read and modify Api Tokens.

    get:
    Return details about a specific token.

    put:
    This can be used to activate a specific token by sending `is_activated: true`.
    """

    model = APIToken
    lookup_field = "id"
    queryset = APIToken.objects.all()
    serializer_class = APITokenSerializer


##
# Experiments
##


class ExperimentList(generics.ListAPIView):
    """ Paginated list of all experiments. Advanced filtering can be done with the `/search` endpoint. """

    model = Experiment
    queryset = Experiment.public_objects.all()
    serializer_class = ExperimentSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = (
        "title",
        "description",
        "accession_code",
        "alternate_accession_code",
        "source_database",
        "source_url",
        "has_publication",
        "publication_title",
        "publication_doi",
        "pubmed_id",
        "organisms",
        "submitter_institution",
        "created_at",
        "last_modified",
        "source_first_published",
        "source_last_modified",
    )


class ExperimentDetail(generics.RetrieveAPIView):
    """ Retrieve details for an experiment given it's accession code """

    lookup_field = "accession_code"
    queryset = Experiment.public_objects.all()
    serializer_class = DetailedExperimentSerializer


##
# Samples
##


@method_decorator(
    name="get",
    decorator=swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="dataset_id",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Filters the result and only returns samples that are added to a dataset.",
            ),
            openapi.Parameter(
                name="experiment_accession_code",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Filters the result and only returns only the samples associated with an experiment accession code.",
            ),
            openapi.Parameter(
                name="accession_codes",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Provide a list of sample accession codes separated by commas and the endpoint will only return information about these samples.",
            ),
        ]
    ),
)
class SampleList(generics.ListAPIView):
    """ Returns detailed information about Samples """

    model = Sample
    serializer_class = DetailedSampleSerializer
    filter_backends = (filters.OrderingFilter, DjangoFilterBackend)
    ordering_fields = "__all__"
    ordering = "-is_processed"
    filterset_fields = (
        "title",
        "organism",
        "source_database",
        "source_archive_url",
        "has_raw",
        "platform_name",
        "technology",
        "manufacturer",
        "sex",
        "age",
        "specimen_part",
        "genotype",
        "disease",
        "disease_stage",
        "cell_line",
        "treatment",
        "race",
        "subject",
        "compound",
        "time",
        "is_processed",
        "is_public",
    )

    def get_queryset(self):
        """
        ref https://www.django-rest-framework.org/api-guide/filtering/#filtering-against-query-parameters
        """
        queryset = (
            Sample.public_objects.prefetch_related("organism")
            .prefetch_related(
                Prefetch("results", queryset=ComputationalResult.objects.order_by("time_start"))
            )
            .prefetch_related("results__processor")
            .prefetch_related("results__computationalresultannotation_set")
            .prefetch_related("results__computedfile_set")
            .filter(**self.get_query_params_filters())
        )

        # case insensitive search https://docs.djangoproject.com/en/2.1/ref/models/querysets/#icontains
        filter_by = self.request.query_params.get("filter_by", None)
        if filter_by:
            queryset = queryset.filter(
                Q(accession_code__icontains=filter_by)
                | Q(title__icontains=filter_by)
                | Q(sex__icontains=filter_by)
                | Q(age__icontains=filter_by)
                | Q(specimen_part__icontains=filter_by)
                | Q(genotype__icontains=filter_by)
                | Q(disease__icontains=filter_by)
                | Q(disease_stage__icontains=filter_by)
                | Q(cell_line__icontains=filter_by)
                | Q(treatment__icontains=filter_by)
                | Q(race__icontains=filter_by)
                | Q(subject__icontains=filter_by)
                | Q(compound__icontains=filter_by)
                | Q(time__icontains=filter_by)
            )

        return queryset

    def get_query_params_filters(self):
        """ We do advanced filtering on the queryset depending on the query parameters.
            This returns the parameters that should be used for that. """
        filter_dict = dict()

        ids = self.request.query_params.get("ids", None)
        if ids is not None:
            ids = [int(x) for x in ids.split(",")]
            filter_dict["pk__in"] = ids

        experiment_accession_code = self.request.query_params.get("experiment_accession_code", None)
        if experiment_accession_code:
            experiment = get_object_or_404(
                Experiment.objects.values("id"), accession_code=experiment_accession_code
            )
            filter_dict["experiments__in"] = [experiment["id"]]

        accession_codes = self.request.query_params.get("accession_codes", None)
        if accession_codes:
            accession_codes = accession_codes.split(",")
            filter_dict["accession_code__in"] = accession_codes

        dataset_id = self.request.query_params.get("dataset_id", None)
        if dataset_id:
            dataset = get_object_or_404(Dataset, id=dataset_id)
            # Python doesn't provide a prettier way of doing this that I know about.
            filter_dict["accession_code__in"] = [
                item for sublist in dataset.data.values() for item in sublist
            ]

        # Accept Organism in both name and ID form
        organism_name = self.request.query_params.get("organism__name", None)
        if organism_name:
            filter_dict["organism__name"] = organism_name

        return filter_dict


class SampleDetail(generics.RetrieveAPIView):
    """ Retrieve the details for a Sample given it's accession code """

    lookup_field = "accession_code"
    queryset = Sample.public_objects.all()
    serializer_class = DetailedSampleSerializer


##
# Processor
##


class ProcessorList(generics.ListAPIView):
    """List all processors."""

    queryset = Processor.objects.all()
    serializer_class = ProcessorSerializer


##
# Results
##


class ComputationalResultsList(generics.ListAPIView):
    """
    computational_results_list

    This lists all `ComputationalResult`. Each one contains meta-information about the output of a computer process. (Ex Salmon).

    This can return valid S3 urls if a valid [token](#tag/token) is sent in the header `HTTP_API_KEY`.
    """

    queryset = ComputationalResult.public_objects.all()

    def get_serializer_class(self):
        token_id = self.request.META.get("HTTP_API_KEY", None)

        try:
            token = APIToken.objects.get(id=token_id, is_activated=True)
            return ComputationalResultWithUrlSerializer
        except Exception:  # General APIToken.DoesNotExist or django.core.exceptions.ValidationError
            return ComputationalResultSerializer

    def filter_queryset(self, queryset):
        filter_dict = self.request.query_params.dict()
        filter_dict.pop("limit", None)
        filter_dict.pop("offset", None)
        return queryset.filter(**filter_dict)


##
# Search Filter Models
##


class OrganismList(generics.ListAPIView):
    """
	  Paginated list of all the available organisms.
	  """

    queryset = Organism.objects.all()
    serializer_class = OrganismSerializer


class PlatformList(generics.ListAPIView):
    """
    Unpaginated list of all the available "platform" information
    """

    serializer_class = PlatformSerializer
    paginator = None

    def get_queryset(self):
        return (
            Sample.public_objects.all()
            .values("platform_accession_code", "platform_name")
            .distinct()
        )


class InstitutionList(generics.ListAPIView):
    """
    Unpaginated list of all the available "institution" information
    """

    serializer_class = InstitutionSerializer
    paginator = None

    def get_queryset(self):
        return Experiment.public_objects.all().values("submitter_institution").distinct()


##
# Jobs
##


class SurveyJobList(generics.ListAPIView):
    """
    List of all SurveyJob.
    """

    model = SurveyJob
    queryset = SurveyJob.objects.all()
    serializer_class = SurveyJobSerializer
    filter_backends = (
        DjangoFilterBackend,
        filters.OrderingFilter,
    )
    filterset_fields = SurveyJobSerializer.Meta.fields
    ordering_fields = ("id", "created_at")
    ordering = ("-id",)


@method_decorator(
    name="get",
    decorator=swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="sample_accession_code",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="List the downloader jobs associated with a sample",
            ),
            openapi.Parameter(
                name="nomad",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Only return jobs that are in the nomad queue currently",
            ),
        ]
    ),
)
class DownloaderJobList(generics.ListAPIView):
    """
    List of all DownloaderJob
    """

    model = DownloaderJob
    serializer_class = DownloaderJobSerializer
    filter_backends = (
        DjangoFilterBackend,
        filters.OrderingFilter,
    )
    filterset_fields = DownloaderJobSerializer.Meta.fields
    ordering_fields = ("id", "created_at")
    ordering = ("-id",)

    def get_queryset(self):
        queryset = DownloaderJob.objects.all()

        sample_accession_code = self.request.query_params.get("sample_accession_code", None)
        if sample_accession_code:
            queryset = queryset.filter(
                original_files__samples__accession_code=sample_accession_code
            ).distinct()

        nomad = self.request.query_params.get("nomad", None)
        if nomad:
            running_nomad_jobs_ids = [
                job["ID"] for job in get_nomad_jobs() if job["Status"] == "running"
            ]
            queryset = queryset.filter(nomad_job_id__in=running_nomad_jobs_ids)

        return queryset


@method_decorator(
    name="get",
    decorator=swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="sample_accession_code",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="List the processor jobs associated with a sample",
            ),
            openapi.Parameter(
                name="nomad",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Only return jobs that are in the nomad queue currently",
            ),
        ]
    ),
)
class ProcessorJobList(generics.ListAPIView):
    """
    List of all ProcessorJobs.
    """

    model = ProcessorJob
    serializer_class = ProcessorJobSerializer
    filter_backends = (
        DjangoFilterBackend,
        filters.OrderingFilter,
    )
    filterset_fields = ProcessorJobSerializer.Meta.fields
    ordering_fields = ("id", "created_at")
    ordering = ("-id",)

    def get_queryset(self):
        queryset = ProcessorJob.objects.all()

        sample_accession_code = self.request.query_params.get("sample_accession_code", None)
        if sample_accession_code:
            queryset = queryset.filter(
                original_files__samples__accession_code=sample_accession_code
            ).distinct()

        nomad = self.request.query_params.get("nomad", None)
        if nomad:
            running_nomad_jobs_ids = [
                job["ID"] for job in get_nomad_jobs() if job["Status"] == "running"
            ]
            queryset = queryset.filter(nomad_job_id__in=running_nomad_jobs_ids)

        return queryset


###
# Statistics
###


def get_start_date(range_param):
    current_date = datetime.now(tz=timezone.utc)
    return {
        "day": current_date - timedelta(days=1),
        "week": current_date - timedelta(weeks=1),
        "month": current_date - timedelta(days=30),
        "year": current_date - timedelta(days=365),
    }.get(range_param)


def paginate_queryset_response(queryset, request):
    paginator = LimitOffsetPagination()
    page_items = paginator.paginate_queryset(queryset, request)
    return Response(
        data={
            "results": [x.to_dict() for x in page_items],
            "limit": paginator.limit,
            "offset": paginator.offset,
            "count": paginator.count,
        },
        status=status.HTTP_200_OK,
    )


class FailedDownloaderJobStats(APIView):
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="range",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Specify a range from which to calculate the possible options",
                enum=("day", "week", "month", "year",),
            )
        ]
    )
    @method_decorator(cache_page(10 * 60))
    def get(self, request, version, format=None):
        range_param = request.query_params.dict().pop("range", "day")
        start_date = get_start_date(range_param)
        jobs = (
            DownloaderJob.objects.filter(created_at__gt=start_date)
            .annotate(reason=Left("failure_reason", 80))
            .values("reason")
            .annotate(
                job_count=Count("reason"),
                sample_count=Count(
                    "original_files__samples",
                    distinct=True,
                    filter=Q(original_files__samples__is_processed=False),
                ),
            )
            .order_by("-job_count")
        )

        return paginate_queryset_response(jobs, request)


class FailedProcessorJobStats(APIView):
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="range",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Specify a range from which to calculate the possible options",
                enum=("day", "week", "month", "year",),
            )
        ]
    )
    @method_decorator(cache_page(10 * 60))
    def get(self, request, version, format=None):
        range_param = request.query_params.dict().pop("range", "day")
        start_date = get_start_date(range_param)
        jobs = (
            ProcessorJob.objects.filter(created_at__gt=start_date)
            .annotate(reason=Left("failure_reason", 80))
            .values("reason")
            .annotate(
                job_count=Count("reason"),
                sample_count=Count(
                    "original_files__samples",
                    distinct=True,
                    filter=Q(original_files__samples__is_processed=False),
                ),
            )
            .order_by("-job_count")
        )

        return paginate_queryset_response(jobs, request)


class AboutStats(APIView):
    """ Returns general stats for the site, used in the about page """

    @method_decorator(cache_page(10 * 60))
    def get(self, request, version, format=None):
        # static values for now
        dummy = request.query_params.dict().pop("dummy", None)
        if dummy:
            # add a dummy response, calculated these on 09/25/2019
            result = {
                "samples_available": 904953 + 391022,
                "total_size_in_bytes": 832195361132962,
                "supported_organisms": 43 + 159,
                "experiments_processed": 35785 + 8661,
            }
            return Response(result)

        result = {
            "samples_available": self._get_samples_available(),
            "total_size_in_bytes": OriginalFile.objects.aggregate(total_size=Sum("size_in_bytes"))[
                "total_size"
            ],
            "supported_organisms": self._get_supported_organisms(),
            "experiments_processed": self._get_experiments_processed(),
        }
        return Response(result)

    def _get_experiments_processed(self):
        """ total experiments with at least one sample processed """
        experiments_with_sample_processed = (
            Experiment.objects.annotate(
                processed_samples_count=Count("samples", filter=Q(samples__is_processed=True)),
            )
            .filter(Q(processed_samples_count__gt=1))
            .count()
        )
        experiments_with_sample_quant = (
            ComputedFile.objects.filter(filename="quant.sf", result__samples__is_processed=False)
            .values_list("result__samples__experiments", flat=True)
            .distinct()
            .count()
        )
        return experiments_with_sample_processed + experiments_with_sample_quant

    def _get_supported_organisms(self):
        """ count organisms with qn targets or that have at least one sample with quant files """
        organisms_with_qn_targets = Organism.objects.filter(qn_target__isnull=False).count()
        organisms_without_qn_targets = (
            Organism.objects.filter(
                qn_target__isnull=True,
                sample__is_processed=False,
                sample__technology="RNA-SEQ",
                sample__results__computedfile__filename="quant.sf",
            )
            .distinct()
            .count()
        )
        return organisms_with_qn_targets + organisms_without_qn_targets

    def _get_samples_available(self):
        """ count the total number of samples that are processed or that have a quant.sf file associated with them """
        processed_samples = Sample.objects.filter(is_processed=True).count()
        unprocessed_samples_with_quant = (
            Sample.objects.filter(
                is_processed=False, technology="RNA-SEQ", results__computedfile__filename="quant.sf"
            )
            .distinct()
            .count()
        )
        return processed_samples + unprocessed_samples_with_quant


class Stats(APIView):
    """ Statistics about the health of the system. """

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="range",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Specify a range from which to calculate the possible options",
                enum=("day", "week", "month", "year",),
            )
        ]
    )
    @method_decorator(cache_page(10 * 60))
    def get(self, request, version, format=None):
        range_param = request.query_params.dict().pop("range", None)
        cached_stats = Stats.calculate_stats(range_param)
        return Response(cached_stats)

    @classmethod
    def calculate_stats(cls, range_param):
        data = {}
        data["generated_on"] = timezone.now()
        data["survey_jobs"] = cls._get_job_stats(SurveyJob.objects, range_param)
        data["downloader_jobs"] = cls._get_job_stats(DownloaderJob.objects, range_param)
        data["processor_jobs"] = cls._get_job_stats(ProcessorJob.objects, range_param)
        data["experiments"] = cls._get_object_stats(Experiment.objects, range_param)

        # processed and unprocessed samples stats
        data["unprocessed_samples"] = cls._get_object_stats(
            Sample.objects.filter(is_processed=False), range_param, "last_modified"
        )
        data["processed_samples"] = cls._get_object_stats(
            Sample.processed_objects, range_param, "last_modified"
        )
        data["processed_samples"]["last_hour"] = cls._samples_processed_last_hour()

        data["processed_samples"]["technology"] = {}
        techs = Sample.processed_objects.values("technology").annotate(count=Count("technology"))
        for tech in techs:
            if not tech["technology"] or not tech["technology"].strip():
                continue
            data["processed_samples"]["technology"][tech["technology"]] = tech["count"]

        data["processed_samples"]["organism"] = {}
        organisms = Sample.processed_objects.values("organism__name").annotate(
            count=Count("organism__name")
        )
        for organism in organisms:
            if not organism["organism__name"]:
                continue
            data["processed_samples"]["organism"][organism["organism__name"]] = organism["count"]

        data["processed_experiments"] = cls._get_object_stats(Experiment.processed_public_objects)
        data["active_volumes"] = list(get_active_volumes())
        data["dataset"] = cls._get_dataset_stats(range_param)

        if range_param:
            data["input_data_size"] = cls._get_input_data_size()
            data["output_data_size"] = cls._get_output_data_size()

        data.update(get_nomad_jobs_breakdown())

        return data

    EMAIL_USERNAME_BLACKLIST = [
        "arielsvn",
        "cansav09",
        "d.prasad",
        "daniel.himmelstein",
        "dv.prasad991",
        "greenescientist",
        "jaclyn.n.taroni",
        "kurt.wheeler91",
        "michael.zietz",
        "miserlou",
    ]

    @classmethod
    def _get_dataset_stats(cls, range_param):
        """Returns stats for processed datasets"""
        filter_query = Q()
        for username in Stats.EMAIL_USERNAME_BLACKLIST:
            filter_query = filter_query | Q(email_address__startswith=username)
        filter_query = filter_query | Q(email_address__endswith="@alexslemonade.org")
        processed_datasets = Dataset.objects.filter(
            is_processed=True, email_address__isnull=False
        ).exclude(filter_query)
        result = processed_datasets.aggregate(
            total=Count("id"),
            aggregated_by_experiment=Count("id", filter=Q(aggregate_by="EXPERIMENT")),
            aggregated_by_species=Count("id", filter=Q(aggregate_by="SPECIES")),
            scale_by_none=Count("id", filter=Q(scale_by="NONE")),
            scale_by_minmax=Count("id", filter=Q(scale_by="MINMAX")),
            scale_by_standard=Count("id", filter=Q(scale_by="STANDARD")),
            scale_by_robust=Count("id", filter=Q(scale_by="ROBUST")),
        )

        if range_param:
            # We don't save the dates when datasets are processed, but we can use
            # `last_modified`, since datasets aren't modified again after they are processed
            result["timeline"] = cls._get_intervals(
                processed_datasets, range_param, "last_modified"
            ).annotate(total=Count("id"), total_size=Sum("size_in_bytes"))
        return result

    @classmethod
    def _samples_processed_last_hour(cls):
        current_date = datetime.now(tz=timezone.utc)
        start = current_date - timedelta(hours=1)
        return Sample.processed_objects.filter(last_modified__range=(start, current_date)).count()

    @classmethod
    def _get_input_data_size(cls):
        total_size = OriginalFile.objects.filter(sample__is_processed=True).aggregate(  # <-- SLOW
            Sum("size_in_bytes")
        )
        return total_size["size_in_bytes__sum"] if total_size["size_in_bytes__sum"] else 0

    @classmethod
    def _get_output_data_size(cls):
        total_size = (
            ComputedFile.public_objects.all()
            .filter(s3_bucket__isnull=False, s3_key__isnull=True)
            .aggregate(Sum("size_in_bytes"))
        )
        return total_size["size_in_bytes__sum"] if total_size["size_in_bytes__sum"] else 0

    @classmethod
    def _get_job_stats(cls, jobs, range_param):
        start_filter = Q()

        if range_param:
            start_date = get_start_date(range_param)
            start_filter = start_filter | Q(start_time__gte=start_date) | Q(start_time__isnull=True)

        result = jobs.filter(start_filter).aggregate(
            total=Count("id"),
            successful=Count("id", filter=Q(success=True)),
            failed=Count("id", filter=Q(success=False)),
            pending=Count(
                "id",
                filter=Q(
                    start_time__isnull=True,
                    success__isnull=True,
                    created_at__gt=JOB_CREATED_AT_CUTOFF,
                ),
            ),
            open=Count(
                "id",
                filter=Q(
                    start_time__isnull=False,
                    success__isnull=True,
                    created_at__gt=JOB_CREATED_AT_CUTOFF,
                ),
            ),
        )
        # via https://stackoverflow.com/questions/32520655/get-average-of-difference-of-datetime-fields-in-django
        result["average_time"] = (
            jobs.filter(start_filter)
            .filter(start_time__isnull=False, end_time__isnull=False, success=True)
            .aggregate(average_time=Avg(F("end_time") - F("start_time")))["average_time"]
        )

        if not result["average_time"]:
            result["average_time"] = 0
        else:
            result["average_time"] = result["average_time"].total_seconds()

        if range_param:
            result["timeline"] = cls._get_intervals(jobs, range_param).annotate(
                total=Count("id"),
                successful=Count("id", filter=Q(success=True)),
                failed=Count("id", filter=Q(success=False)),
                pending=Count("id", filter=Q(start_time__isnull=True, success__isnull=True)),
                open=Count("id", filter=Q(start_time__isnull=False, success__isnull=True)),
            )

        return result

    @classmethod
    def _get_object_stats(cls, objects, range_param=False, field="created_at"):
        result = {"total": objects.count()}

        if range_param:
            result["timeline"] = cls._get_intervals(objects, range_param, field).annotate(
                total=Count("id")
            )

        return result

    @classmethod
    def _get_intervals(cls, objects, range_param, field="last_modified"):
        range_to_trunc = {"day": "hour", "week": "day", "month": "day", "year": "month"}

        # truncate the parameterized field so it can be annotated by range
        # ie. each day is composed of 24 hours...
        start_trunc = Trunc(field, range_to_trunc.get(range_param), output_field=DateTimeField())

        # get the correct start time for the range
        start_range = get_start_date(range_param)

        # annotate and filter in a single query
        # ref https://stackoverflow.com/a/38359913/763705
        return objects.annotate(start=start_trunc).values("start").filter(start__gte=start_range)


###
# Transcriptome Indices
###
@method_decorator(
    name="get",
    decorator=swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="organism_name",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Organism name. Eg. `MUS_MUSCULUS`",
            ),
            openapi.Parameter(
                name="length",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Short hand for `index_type` Eg. `short` or `long`",
            ),
            openapi.Parameter(
                name="salmon_version",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Eg. `salmon 0.13.1`",
            ),
            openapi.Parameter(
                name="index_type",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Eg. `TRANSCRIPTOME_LONG`",
            ),
        ]
    ),
)
class TranscriptomeIndexList(generics.ListAPIView):
    """
    List all Transcriptome Indices. These are a special type of process result,
    necessary for processing other SRA samples.
    """

    serializer_class = OrganismIndexSerializer
    filter_backends = (
        DjangoFilterBackend,
        filters.OrderingFilter,
    )
    filterset_fields = ["salmon_version", "index_type"]
    ordering_fields = ("created_at", "salmon_version")
    ordering = ("-created_at",)

    def get_queryset(self):
        queryset = OrganismIndex.public_objects.all()

        organism_name = self.request.GET.get("organism_name", None)
        if organism_name is not None:
            queryset = queryset.filter(organism__name=organism_name.upper())

        length = self.request.GET.get("length", None)
        if length is not None:
            index_type = "TRANSCRIPTOME_{}".format(length.upper())
            queryset = queryset.filter(index_type=index_type)

        return queryset


@method_decorator(
    name="get",
    decorator=swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="id",
                in_=openapi.IN_PATH,
                type=openapi.TYPE_NUMBER,
                description="Transcriptome Index Id eg `1`",
            ),
        ]
    ),
)
class TranscriptomeIndexDetail(generics.RetrieveAPIView):
    """
    Gets the S3 url associated with the organism and length, along with other metadata about
    the transcriptome index we have stored.
    """

    serializer_class = OrganismIndexSerializer
    lookup_field = "id"
    queryset = OrganismIndex.public_objects.all()


###
# Compendia
###
@method_decorator(
    name="get",
    decorator=swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="latest_version",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_BOOLEAN,
                description="`True` will only return the highest `compendium_version` for each primary_organism.",
            ),
            openapi.Parameter(
                name="quant_sf_only",
                in_=openapi.IN_QUERY,
                type=openapi.TYPE_BOOLEAN,
                description="`True` for RNA-seq Sample Compendium results or `False` for quantile normalized.",
            ),
        ]
    ),
)
class CompendiumResultList(generics.ListAPIView):
    """
    List all CompendiaResults with filtering.
    """

    model = CompendiumResult
    queryset = CompendiumResult.objects.all()
    filter_backends = (
        DjangoFilterBackend,
        filters.OrderingFilter,
    )
    filterset_fields = ["primary_organism__name", "compendium_version", "quant_sf_only"]
    ordering_fields = ("primary_organism__name", "compendium_version", "id")
    ordering = ("primary_organism__name",)

    def get_queryset(self):
        public_result_queryset = CompendiumResult.objects.filter(result__is_public=True)
        latest_version = self.request.query_params.get("latest_version", False)
        if latest_version:
            version_filter = Q(
                primary_organism=OuterRef("primary_organism"),
                quant_sf_only=OuterRef("quant_sf_only"),
            )
            latest_version = (
                public_result_queryset.filter(version_filter)
                .order_by("-compendium_version")
                .values("compendium_version")
            )
            return public_result_queryset.annotate(
                latest_version=Subquery(latest_version[:1])
            ).filter(compendium_version=F("latest_version"))

        return public_result_queryset

    def get_serializer_class(self):
        try:
            token_id = self.request.META.get("HTTP_API_KEY", None)
            token = APIToken.objects.get(id=token_id, is_activated=True)
            return CompendiumResultWithUrlSerializer
        except Exception:  # General APIToken.DoesNotExist or django.core.exceptions.ValidationError
            return CompendiumResultSerializer


class CompendiumResultDetails(generics.RetrieveAPIView):
    """
    Get a specific Compendium Result
    """

    model = CompendiumResult
    queryset = CompendiumResult.objects.filter(is_public=True)
    lookup_field = "id"

    def get_serializer_class(self):
        try:
            token_id = self.request.META.get("HTTP_API_KEY", None)
            token = APIToken.objects.get(id=token_id, is_activated=True)
            return CompendiumResultWithUrlSerializer
        except Exception:  # General APIToken.DoesNotExist or django.core.exceptions.ValidationError
            return CompendiumResultSerializer


###
# QN Targets
###


class QNTargetsAvailable(generics.ListAPIView):
    """
    This is a list of all of the organisms which have available QN Targets
    """

    serializer_class = OrganismSerializer
    paginator = None

    def get_queryset(self):
        return Organism.get_objects_with_qn_targets()


@method_decorator(
    name="get",
    decorator=swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                name="organism_name",
                in_=openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                description="Eg `DANIO_RERIO`, `MUS_MUSCULUS`",
            )
        ],
        responses={404: "QN Target not found for the given organism."},
    ),
)
class QNTargetsDetail(generics.RetrieveAPIView):
    """
    Get a detailed view of the Quantile Normalization file for an organism.
    """

    serializer_class = QNTargetSerializer

    def get_object(self):
        organism = self.kwargs["organism_name"]
        organism = organism.upper().replace(" ", "_")
        try:
            organism_id = Organism.get_object_for_name(organism).id
            annotation = (
                ComputationalResultAnnotation.objects.filter(
                    data__organism_id=organism_id, data__is_qn=True
                )
                .order_by("-created_at")
                .first()
            )
            qn_target = annotation.result.computedfile_set.first()
        except Exception:
            raise NotFound("Don't have a target for that organism!")
        if not qn_target:
            raise NotFound("Don't have a target for that organism!!")
        return qn_target


##
# Computed Files
##


class ComputedFilesList(generics.ListAPIView):
    """
    computed_files_list

    ComputedFiles are representation of files created by refinebio processes.

    It's possible to download each one of these files by providing a valid token. To
    acquire and activate an API key see the documentation for the [/token](#tag/token) endpoint.
    When a valid token is provided the url will be sent back in the field `download_url`. Example:

    ```py
    import requests
    import json

    headers = {
        'Content-Type': 'application/json',
        'API-KEY': token_id # requested from /token
    }
    requests.get('https://api.refine.bio/v1/computed_files/?id=5796866', {}, headers=headers)
    ```

    This endpoint can also be used to fetch all the compendia files we have generated with:
    ```
    GET /computed_files?is_compendia=True&is_public=True
    ```
    """

    queryset = ComputedFile.objects.all()
    serializer_class = ComputedFileListSerializer
    filter_backends = (
        DjangoFilterBackend,
        filters.OrderingFilter,
    )
    filterset_fields = (
        "id",
        "samples",
        "is_qn_target",
        "is_smashable",
        "is_qc",
        "is_compendia",
        "quant_sf_only",
        "svd_algorithm",
        "compendia_version",
        "created_at",
        "last_modified",
    )
    ordering_fields = (
        "id",
        "created_at",
        "last_modified",
        "compendia_version",
    )
    ordering = ("-id",)

    def get_serializer_context(self):
        """
        Extra context provided to the serializer class.
        """
        serializer_context = super(ComputedFilesList, self).get_serializer_context()
        token_id = self.request.META.get("HTTP_API_KEY", None)
        try:
            token = APIToken.objects.get(id=token_id, is_activated=True)
            return {**serializer_context, "token": token}
        except Exception:  # General APIToken.DoesNotExist or django.core.exceptions.ValidationError
            return serializer_context


class OriginalFileList(generics.ListAPIView):
    """
    original_files_list

    List Original Files that are associated with Samples. These are the files we proccess.

    """

    queryset = OriginalFile.objects.all()
    serializer_class = OriginalFileListSerializer
    filter_backends = (
        DjangoFilterBackend,
        filters.OrderingFilter,
    )
    filterset_fields = OriginalFileListSerializer.Meta.fields
    ordering_fields = (
        "id",
        "created_at",
        "last_modified",
    )
    ordering = ("-id",)


# error handlers
def handle404error(request, exception):
    message = "The requested resource was not found on this server."
    url = "https://api.refine.bio/"

    # check to see if the 404ed request contained a version
    if not match(r"^/v[1-9]/.*", request.path):
        message = "refine.bio API resources are only available through versioned requests."

    return JsonResponse({"message": message, "docs": url, "status_code": 404,}, status=404)


def handle500error(request):
    return JsonResponse(
        {"message": "A server error occured. This has been reported.", "status_code": 500,},
        status=500,
    )


##
# Util
##


def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0]
    else:
        ip = request.META.get("REMOTE_ADDR", "")
    return ip
