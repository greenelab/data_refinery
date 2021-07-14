from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Count
from django.db.models.expressions import Q
from django.utils import timezone

from data_refinery_common.models.managers import ProcessedPublicObjectsManager, PublicObjectsManager


class Experiment(models.Model):
    """An Experiment or Study"""

    class Meta:
        db_table = "experiments"
        base_manager_name = "public_objects"

    def __str__(self):
        return "Experiment: " + self.accession_code

    # Managers
    objects = models.Manager()
    public_objects = PublicObjectsManager()
    processed_public_objects = ProcessedPublicObjectsManager()

    # Relations
    samples = models.ManyToManyField("Sample", through="ExperimentSampleAssociation")
    organisms = models.ManyToManyField("Organism", through="ExperimentOrganismAssociation")

    # Identifiers
    accession_code = models.CharField(max_length=64, unique=True)
    alternate_accession_code = models.CharField(max_length=64, unique=True, null=True)

    # Historical Properties
    source_database = models.CharField(max_length=32)  # "ArrayExpress, "SRA", "GEO"
    source_url = models.TextField()

    # Properties
    # I was always under the impression that TextFields were slower
    # than CharFields, however the Postgres documentation disagrees:
    # https://www.postgresql.org/docs/9.0/static/datatype-character.html
    title = models.TextField()
    description = models.TextField()
    protocol_description = models.JSONField(default=dict)
    technology = models.CharField(max_length=256, blank=True)
    submitter_institution = models.CharField(max_length=256, blank=True)
    has_publication = models.BooleanField(default=False)
    publication_title = models.TextField(default="")
    publication_doi = models.CharField(max_length=64, blank=True)
    publication_authors = ArrayField(models.TextField(), default=list)
    pubmed_id = models.CharField(max_length=32, blank=True)
    source_first_published = models.DateTimeField(null=True)
    source_last_modified = models.DateTimeField(null=True)

    # Cached Computed Properties
    num_total_samples = models.IntegerField(default=0)
    num_processed_samples = models.IntegerField(default=0)
    num_downloadable_samples = models.IntegerField(default=0)
    sample_keywords = ArrayField(models.TextField(), default=list)
    sample_metadata_fields = ArrayField(models.TextField(), default=list)
    platform_names = ArrayField(models.TextField(), default=list)
    platform_accession_codes = ArrayField(models.TextField(), default=list)

    # Common Properties
    is_public = models.BooleanField(default=True)
    created_at = models.DateTimeField(editable=False, default=timezone.now)
    last_modified = models.DateTimeField(default=timezone.now)

    def save(self, *args, **kwargs):
        """On save, update timestamps"""
        current_time = timezone.now()
        if not self.id:
            self.created_at = current_time
        self.last_modified = current_time

        if self.accession_code and not self.alternate_accession_code:
            if self.accession_code.startswith("GSE"):
                self.alternate_accession_code = "E-GEOD-" + self.accession_code[3:]
            elif self.accession_code.startswith("E-GEOD-"):
                self.alternate_accession_code = "GSE" + self.accession_code[7:]

        return super(Experiment, self).save(*args, **kwargs)

    def update_num_samples(self):
        """Update our cache values"""
        aggregates = self.samples.aggregate(
            num_total_samples=Count("id"),
            num_processed_samples=Count("id", filter=Q(is_processed=True)),
            num_downloadable_samples=Count(
                "id", filter=Q(is_processed=True, organism__qn_target__isnull=False)
            ),
        )
        self.num_total_samples = aggregates["num_total_samples"]
        self.num_processed_samples = aggregates["num_processed_samples"]
        self.num_downloadable_samples = aggregates["num_downloadable_samples"]
        self.save()

    def to_metadata_dict(self):
        """Render this Experiment as a dict"""

        metadata = {}
        metadata["title"] = self.title
        metadata["accession_code"] = self.accession_code
        metadata["organisms"] = list(self.organisms.all().values_list("name", flat=True))
        metadata["sample_accession_codes"] = list(
            self.samples.all().values_list("accession_code", flat=True)
        )
        metadata["description"] = self.description
        metadata["protocol_description"] = self.protocol_description
        metadata["technology"] = self.technology
        metadata["submitter_institution"] = self.submitter_institution
        metadata["has_publication"] = self.has_publication
        metadata["publication_title"] = self.publication_title
        metadata["publication_doi"] = self.publication_doi
        metadata["pubmed_id"] = self.pubmed_id
        if self.source_first_published:
            metadata["source_first_published"] = self.source_first_published.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
        else:
            metadata["source_first_published"] = ""
        if self.source_last_modified:
            metadata["source_last_modified"] = self.source_last_modified.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
        else:
            metadata["source_last_modified"] = ""

        return metadata

    def get_sample_keywords(self):
        """Get the human-readable name of all of the keywords that are defined
        on at least one sample
        """
        keywords = set()

        for sample in self.samples.all():
            keywords |= set(sample.keywords.values_list("name__human_readable_name", flat=True))

        return list(keywords)

    def get_sample_metadata_fields(self):
        """Get all metadata fields that are non-empty for at least one sample in the experiment.
        See https://github.com/AlexsLemonade/refinebio-frontend/issues/211 for why this is needed.
        """
        fields = []

        possible_fields = [
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
        ]
        samples = self.samples.all()
        for field in possible_fields:
            for sample in samples:
                if getattr(sample, field) != None and getattr(sample, field) != "":
                    fields.append(field)
                    break

        return fields

    def update_sample_keywords(self):
        self.sample_keywords = self.get_sample_keywords()

    def update_sample_metadata_fields(self):
        self.sample_metadata_fields = self.get_sample_metadata_fields()

    def update_platform_names(self):
        self.platform_names = self.get_platform_names()
        self.platform_accession_codes = self.get_platform_accession_codes()

    def get_sample_technologies(self):
        """Get a list of unique technologies for all of the associated samples"""
        return list(set([sample.technology for sample in self.samples.all()]))

    def get_platform_names(self):
        """Get a list of unique platforms for all of the associated samples"""
        return list(set([sample.platform_name for sample in self.samples.all()]))

    def get_platform_accession_codes(self):
        """Get a list of unique platforms for all of the associated samples"""
        return list(set([sample.platform_accession_code for sample in self.samples.all()]))

    @property
    def platforms(self):
        """Returns a list of related pipelines"""
        return list(set([sample.platform_name for sample in self.samples.all()]))

    @property
    def pretty_platforms(self):
        """Returns a prettified list of related pipelines"""
        return list(set([sample.pretty_platform for sample in self.samples.all()]))

    @property
    def processed_samples(self):
        return list(
            [sample.accession_code for sample in self.samples.all() if sample.is_processed == True]
        )

    @property
    def downloadable_organism_names(self):
        """Get a list of unique organism names that has at least one downloadable sample"""
        result = (
            self.samples.filter(is_processed=True, organism__qn_target__isnull=False)
            .values_list("organism__name", flat=True)
            .distinct()
        )

        return list(result)

    @property
    def organism_names(self):
        """Get a list of unique organism names"""
        result = self.samples.values_list("organism__name", flat=True).distinct()

        return list(result)

    @property
    def downloadable_samples(self):
        """
        Returns the accession codes of the downloadable samples in this experiment.
        This is indexed on elastic search and used to count the number of samples
        on the filters.
        """
        return list(
            self.samples.filter(is_processed=True, organism__qn_target__isnull=False).values_list(
                "accession_code", flat=True
            )
        )
