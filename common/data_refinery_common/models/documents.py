from django_elasticsearch_dsl import Document, Index, fields
from elasticsearch_dsl import analyzer
from elasticsearch_dsl.analysis import token_filter

from data_refinery_common.models.experiment import Experiment

experiment_index = Index("experiments")
experiment_index.settings(number_of_shards=1, number_of_replicas=0, max_result_window=9999999)

# via https://django-elasticsearch-dsl-drf.readthedocs.io/en/0.17.2/advanced_usage_examples.html?highlight=ngram#id8
# via https://github.com/barseghyanartur/django-elasticsearch-dsl-drf/issues/110
edge_ngram_completion_filter = token_filter(
    "edge_ngram_completion_filter", type="edge_ngram", min_gram=3, max_gram=12
)
html_strip = analyzer(
    "html_strip",
    tokenizer="whitespace",
    filter=[edge_ngram_completion_filter, "standard", "lowercase", "stop", "snowball"],
    char_filter=["html_strip"],
)
html_strip_no_ngram = analyzer(
    "html_strip_no_ngram",
    tokenizer="standard",
    filter=["standard", "lowercase", "stop"],
    char_filter=["html_strip"],
)
html_strip_no_stop = analyzer(
    "html_strip_no_stop",
    tokenizer="whitespace",
    filter=["standard", "lowercase"],
    char_filter=["html_strip"],
)
standard_keyword = analyzer("standard_keyword", tokenizer="keyword", filter=[],)


@experiment_index.doc_type
class ExperimentDocument(Document):
    """Our Experiment ElasticSearch Document, which
    corresponds to our Experiment model."""

    # Keyword Fields
    title = fields.TextField(
        analyzer=html_strip, fielddata=True, fields={"raw": fields.KeywordField()}
    )
    publication_title = fields.TextField(
        analyzer=html_strip, fielddata=True, fields={"raw": fields.KeywordField()}
    )
    description = fields.TextField(
        analyzer=html_strip, fielddata=True, fields={"raw": fields.KeywordField()}
    )
    publication_authors = fields.TextField(
        analyzer=html_strip, fielddata=True, fields={"raw": fields.KeywordField()}
    )
    technology = fields.TextField(
        analyzer=html_strip_no_stop, fielddata=True, fields={"raw": fields.KeywordField()}
    )
    organism_names = fields.TextField(
        analyzer=html_strip_no_ngram, fielddata=True, fields={"raw": fields.KeywordField()}
    )
    downloadable_organism_names = fields.TextField(
        analyzer=html_strip_no_ngram, fielddata=True, fields={"raw": fields.KeywordField()}
    )
    platform_names = fields.TextField(
        analyzer=standard_keyword, fielddata=True, fields={"raw": fields.TextField()}
    )
    platform_accession_codes = fields.TextField(
        analyzer=standard_keyword, fielddata=True, fields={"raw": fields.TextField()}
    )

    # Basic Fields
    accession_code = fields.KeywordField()
    alternate_accession_code = fields.KeywordField()
    submitter_institution = fields.TextField()
    publication_doi = fields.TextField()
    has_publication = fields.BooleanField()
    sample_metadata_fields = fields.TextField()
    pubmed_id = fields.TextField()
    num_total_samples = fields.IntegerField()
    num_processed_samples = fields.IntegerField()
    num_downloadable_samples = fields.IntegerField()
    source_first_published = fields.DateField()

    # Index all downloadable samples as keywords so that we can calculate unique counts on the facets
    downloadable_samples = fields.ListField(fields.KeywordField())

    # Index our sample keywords so that we can use them for better search
    sample_keywords = fields.ListField(fields.KeywordField())

    class Django:
        model = Experiment
        parallel_indexing = True
        queryset_pagination = 3000

        fields = [
            "id",
        ]

    def get_queryset(self):
        """Override default queryset"""
        return super(ExperimentDocument, self).get_queryset().order_by("id")
