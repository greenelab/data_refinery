import json

from django.core.cache import cache
from django.core.exceptions import TooManyFieldsSent
from django.core.management import call_command
from django.urls import reverse
from rest_framework.test import APITestCase

from data_refinery_api.test.test_api_general import API_VERSION
from data_refinery_common.models import (
    ComputationalResult,
    ComputationalResultAnnotation,
    DownloaderJob,
    DownloaderJobOriginalFileAssociation,
    Experiment,
    ExperimentAnnotation,
    ExperimentSampleAssociation,
    Organism,
    OriginalFile,
    OriginalFileSampleAssociation,
    ProcessorJob,
    ProcessorJobOriginalFileAssociation,
    Sample,
    SampleAnnotation,
    SampleResultAssociation,
)
from data_refinery_common.models.documents import ExperimentDocument

ECOLI_STRAIN_NAME = "Escherichia coli str. k-12 substr. mg1655"


class ESTestCases(APITestCase):
    @classmethod
    def setUpClass(cls):
        super(ESTestCases, cls).setUpClass()  # ref https://stackoverflow.com/a/29655301/763705

        """
        #Set up class.
        """
        experiment = Experiment()
        experiment.accession_code = "GSE000-X"
        experiment.title = "NONONONO"
        experiment.description = "Boooooourns. Wasabi."
        experiment.technology = "RNA-SEQ"
        experiment.save()

        experiment = Experiment()
        experiment.accession_code = "GSE123-X"
        experiment.title = "Hey Ho Let's Go"
        experiment.description = (
            "This is a very exciting test experiment. Faygo soda. Blah blah blah."
        )
        experiment.technology = "MICROARRAY"
        experiment.num_processed_samples = 1  # added below
        experiment.num_total_samples = 1
        experiment.num_downloadable_samples = 1
        experiment.save()

        experiment_annotation = ExperimentAnnotation()
        experiment_annotation.data = {"hello": "world", "123": 456}
        experiment_annotation.experiment = experiment
        experiment_annotation.save()

        sample = Sample()
        sample.title = "123"
        sample.accession_code = "123"
        sample.save()

        organism = Organism(name=ECOLI_STRAIN_NAME, taxonomy_id=879462, is_scientific_name=True,)
        organism.save()

        sample = Sample()
        sample.title = "789"
        sample.accession_code = "789"
        sample.is_processed = True
        sample.organism = organism
        sample.save()

        sample_annotation = SampleAnnotation()
        sample_annotation.data = {"goodbye": "world", "789": 123}
        sample_annotation.sample = sample
        sample_annotation.save()

        original_file = OriginalFile()
        original_file.save()

        original_file_sample_association = OriginalFileSampleAssociation()
        original_file_sample_association.sample = sample
        original_file_sample_association.original_file = original_file
        original_file_sample_association.save()

        downloader_job = DownloaderJob()
        downloader_job.save()

        download_assoc = DownloaderJobOriginalFileAssociation()
        download_assoc.original_file = original_file
        download_assoc.downloader_job = downloader_job
        download_assoc.save()

        processor_job = ProcessorJob(downloader_job=downloader_job)
        processor_job.save()

        processor_assoc = ProcessorJobOriginalFileAssociation()
        processor_assoc.original_file = original_file
        processor_assoc.processor_job = processor_job
        processor_assoc.save()

        # associate the experiment with the sample
        experiment_sample_association = ExperimentSampleAssociation()
        experiment_sample_association.sample = sample
        experiment_sample_association.experiment = experiment
        experiment_sample_association.save()

        result = ComputationalResult()
        result.save()

        # and create a qn tarjet for the sample
        computational_result = ComputationalResultAnnotation()
        computational_result.result = result
        computational_result.data = {"is_qn": True, "organism_id": sample.organism.id}
        computational_result.save()

        # and associate it with the sample organism
        sample.organism.qn_target = result
        sample.organism.save()

        sra = SampleResultAssociation()
        sra.sample = sample
        sra.result = result
        sra.save()

        result = ComputationalResult()
        result.save()

        sra = SampleResultAssociation()
        sra.sample = sample
        sra.result = result
        sra.save()

        # clear default cache and reindex
        # otherwise the organisms with qn_targes will be cached.
        cache.clear()
        call_command("search_index", "--rebuild", "-f")

    def test_es_endpoint(self):
        """
        #Test basic ES functionality
        """
        response = self.client.get(reverse("search", kwargs={"version": API_VERSION}))

        """
        #Test basic ES functionality
        """
        es_search_result = ExperimentDocument.search().filter("term", description="soda")
        es_search_result_qs = es_search_result.to_queryset()
        self.assertEqual(len(es_search_result_qs), 1)

        # Sanity
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)

        # test sample counts in filters
        self.assertEqual(response.json()["facets"]["has_publication"]["false"], 1)
        self.assertEqual(response.json()["facets"]["technology"]["microarray"], 1)
        self.assertEqual(response.json()["facets"]["technology"]["rna-seq"], 0)
        self.assertEqual(
            list(response.json()["facets"]["downloadable_organism_names"].keys()),
            [ECOLI_STRAIN_NAME],
        )
        self.assertEqual(
            response.json()["facets"]["downloadable_organism_names"][ECOLI_STRAIN_NAME], 1
        )

        # Basic Search
        response = self.client.get(
            reverse("search", kwargs={"version": API_VERSION}), {"search": "soda"}
        )
        self.assertEqual(response.json()["count"], 1)
        cache.clear()

        # Positive filter result
        response = self.client.get(
            reverse("search", kwargs={"version": API_VERSION}),
            {"search": "soda", "technology": "microarray"},
        )
        self.assertEqual(response.json()["count"], 1)
        cache.clear()

        # Negative filter result
        response = self.client.get(
            reverse("search", kwargs={"version": API_VERSION}),
            {"search": "soda", "technology": "rna"},
        )
        self.assertEqual(response.json()["count"], 0)

        # Filter based on organism name
        response = self.client.get(
            reverse("search", kwargs={"version": API_VERSION}),
            {"search": "soda", "organism": ECOLI_STRAIN_NAME},
        )
        self.assertEqual(response.json()["count"], 1)

    def test_es_endpoint_post(self):
        # Basic filter
        search = {"accession_code": "GSE123-X"}
        response = self.client.post(
            reverse("search", kwargs={"version": API_VERSION}),
            json.dumps(search),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        cache.clear()

        # __in filter
        search = {"accession_code__in": ["GSE123-X"]}
        response = self.client.post(
            reverse("search", kwargs={"version": API_VERSION}),
            json.dumps(search),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        cache.clear()

        # Numeric filter
        search = {"num_downloadable_samples__gt": 0}
        response = self.client.post(
            reverse("search", kwargs={"version": API_VERSION}),
            json.dumps(search),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        cache.clear()

        # Large query
        search = {"accession_code__in": ["GSE123-X" for _ in range(10000)]}
        with self.assertRaises(TooManyFieldsSent):
            # This should fail for GET requests because the generated URL is too large
            response = self.client.get(reverse("search", kwargs={"version": API_VERSION}), search)

        response = self.client.post(
            reverse("search", kwargs={"version": API_VERSION}),
            json.dumps(search),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
