##
# Experiment document views
##

from django.http import QueryDict
from django.utils.decorators import method_decorator
from rest_framework import serializers
from rest_framework.renderers import BrowsableAPIRenderer, JSONRenderer

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
from django_elasticsearch_dsl_drf.serializers import DocumentSerializer
from django_elasticsearch_dsl_drf.viewsets import DocumentViewSet
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from elasticsearch_dsl import TermsFacet
from six import iteritems

from data_refinery_api.exceptions import InvalidFilters
from data_refinery_common.models.documents import ExperimentDocument


class FormlessBrowsableAPIRenderer(BrowsableAPIRenderer):
    """A BrowsableAPIRenderer that never tries to display a form for any
    method.

    We use this in ExperimentDocumentView because otherwise we get an error
    when trying to generate the form for POST requests.
    """

    def show_form_for_method(self, view, method, request, instance):
        return False


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
                raise InvalidFilters(
                    message="Invalid type {} for filter value {}".format(type(value), str(value))
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


class ExperimentDocumentSerializer(DocumentSerializer):
    """Serializer for the Experiment document."""

    class Meta(object):
        """Meta options."""

        document = ExperimentDocument
        fields = (
            "id",
            "title",
            "publication_title",
            "description",
            "technology",
            "accession_code",
            "alternate_accession_code",
            "submitter_institution",
            "has_publication",
            "publication_doi",
            "publication_authors",
            "sample_metadata_fields",
            "platform_names",
            "platform_accession_codes",
            "organism_names",
            "downloadable_organism_names",
            "pubmed_id",
            "num_total_samples",
            "num_processed_samples",
            "num_downloadable_samples",
            "source_first_published",
        )
        read_only_fields = fields


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
    """ElasticSearch powered experiment search."""

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
        "sample_keywords": {"boost": 7},
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
        "organism": "organism_names.raw",
        "downloadable_organism": "downloadable_organism_names.raw",
        "num_processed_samples": {
            "field": "num_processed_samples",
            "lookups": [LOOKUP_FILTER_RANGE, LOOKUP_QUERY_IN, LOOKUP_QUERY_GT],
        },
        "num_downloadable_samples": {
            "field": "num_downloadable_samples",
            "lookups": [LOOKUP_FILTER_RANGE, LOOKUP_QUERY_IN, LOOKUP_QUERY_GT],
        },
        "sample_keywords": "sample_keywords",
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
        "downloadable_organism_names": {
            "field": "downloadable_organism_names.raw",
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
