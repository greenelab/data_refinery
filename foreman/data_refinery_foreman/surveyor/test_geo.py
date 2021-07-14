from unittest.mock import patch

from django.test import TransactionTestCase

import vcr

from data_refinery_common.models import (
    DownloaderJob,
    OriginalFile,
    Sample,
    SurveyJob,
    SurveyJobKeyValue,
)
from data_refinery_foreman.surveyor.geo import GeoSurveyor
from data_refinery_foreman.surveyor.utils import get_title_and_authors_for_pubmed_id


class SurveyTestCase(TransactionTestCase):
    def prep_test(self, experiment_accession):
        survey_job = SurveyJob(source_type="GEO")
        survey_job.save()
        self.survey_job = survey_job

        key_value_pair = SurveyJobKeyValue(
            survey_job=survey_job, key="experiment_accession_code", value=experiment_accession
        )
        key_value_pair.save()

    def tearDown(self):
        DownloaderJob.objects.all().delete()
        SurveyJobKeyValue.objects.all().delete()
        SurveyJob.objects.all().delete()

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.geo.geo_survey_microarray.yaml")
    @patch("data_refinery_foreman.surveyor.external_source.send_job")
    def test_geo_survey_microarray(self, mock_send_task):
        """ Run the GEO surveyor and make sure we get some files to DL!

        For an Illumina Microarray platform.
        """
        self.prep_test("GSE11915")

        geo_surveyor = GeoSurveyor(self.survey_job)
        geo_surveyor.survey()

        self.assertEqual(34, Sample.objects.all().count())

        sample_object = Sample.objects.first()
        self.assertEqual(
            sample_object.platform_name, "[HG-U133A] Affymetrix Human Genome U133A Array"
        )
        self.assertEqual(sample_object.platform_accession_code, "hgu133a")
        self.assertEqual(sample_object.technology, "MICROARRAY")

        # Confirm sample protocol_info
        GSM299800 = Sample.objects.get(accession_code="GSM299800")
        protocol_info = GSM299800.protocol_info
        self.assertEqual(
            protocol_info["Extraction protocol"],
            [
                "Chromatin IP performed as described in Odom et al., Science 303, 1378 (Feb 27, 2004)"
            ],
        )
        self.assertEqual(protocol_info["Data processing"], ["Z-score normalization"])

        downloader_jobs = DownloaderJob.objects.all()
        self.assertEqual(45, downloader_jobs.count())

        # Make sure there aren't extra OriginalFiles
        original_files = OriginalFile.objects.all()
        self.assertEqual(45, original_files.count())

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.geo.geo_survey_GSE6532.yaml")
    @patch("data_refinery_foreman.surveyor.external_source.send_job")
    def test_geo_survey_GSE6532(self, mock_send_task):
        """This experiment didn't get surveyed correctly until refactoring harmonizer.

        Seemed like a good test to make permanent.
        """
        self.prep_test("GSE6532")

        geo_surveyor = GeoSurveyor(self.survey_job)
        geo_surveyor.survey()

        self.assertEqual(741, Sample.objects.all().count())

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.geo.geo_survey_not_agilent.yaml")
    @patch("data_refinery_foreman.surveyor.external_source.send_job")
    def test_geo_survey_not_agilent(self, mock_send_task):
        """ Test to make sure we're setting MFG correctly
        """
        self.prep_test("GSE34198")

        geo_surveyor = GeoSurveyor(self.survey_job)
        geo_surveyor.survey()

        sample_object = Sample.objects.first()
        self.assertEqual(sample_object.manufacturer, "ILLUMINA")

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.geo.geo_survey_agilent.yaml")
    @patch("data_refinery_foreman.surveyor.external_source.send_job")
    def test_geo_survey_agilent(self, mock_send_task):
        """ Run the GEO surveyor and make sure we get some files to DL!

        For an Agilent Microarray platform.
        """
        self.prep_test("GSE35186")

        geo_surveyor = GeoSurveyor(self.survey_job)
        geo_surveyor.survey()

        self.assertEqual(124, Sample.objects.all().count())

        sample_object = Sample.objects.first()
        self.assertEqual(
            sample_object.platform_name,
            "Agilent-014850 Whole Human Genome Microarray 4x44K G4112F (Probe Name version)",
        )
        self.assertEqual(sample_object.platform_accession_code, "GPL6480")
        # We currently do not support Agilent platforms, so we can't
        # match its accession to one we know about.
        self.assertEqual(sample_object.technology, "UNKNOWN")

        downloader_jobs = DownloaderJob.objects.all()
        # There would be 124 samples + 2 metadata files. However at
        # the moment Agilent is unsupported so we don't want to queue
        # downloader jobs.
        self.assertEqual(0, downloader_jobs.count())

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.geo.geo_survey_rnaseq.yaml")
    @patch("data_refinery_foreman.surveyor.external_source.send_job")
    def test_geo_survey_rnaseq(self, mock_send_task):
        """Run the GEO surveyor and make sure we discover the experiment/samples.

        For an Illumina RNASeq platform. However it shouldn't actually
        queue any downloader jobs because its RNA-Seq data coming from
        GEO.
        """
        self.prep_test("GSE99264")

        geo_surveyor = GeoSurveyor(self.survey_job)
        geo_surveyor.survey()

        self.assertEqual(7, Sample.objects.all().count())

        sample_object = Sample.objects.first()
        self.assertEqual(sample_object.platform_name, "Illumina Genome Analyzer II")
        self.assertEqual(sample_object.platform_accession_code, "Illumina Genome Analyzer II")
        self.assertEqual(sample_object.technology, "RNA-SEQ")

        downloader_jobs = DownloaderJob.objects.all()
        self.assertEqual(0, downloader_jobs.count())

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.geo.geo_survey_superseries.yaml")
    @patch("data_refinery_foreman.surveyor.external_source.send_job")
    def test_geo_survey_superseries(self, mock_send_task):
        """Run the GEO surveyor and make sure we get some files to DL!

        For a Super Series. But also that we don't queue downloader
        jobs for RNA-Seq samples coming from GEO.
        """
        self.prep_test("GSE103217")

        geo_surveyor = GeoSurveyor(self.survey_job)
        geo_surveyor.survey()

        # 28 total samples
        self.assertEqual(28, Sample.objects.all().count())

        # 10 of which are microarray and therefore need downloader jobs
        microarray_samples = Sample.objects.filter(technology="MICROARRAY")
        self.assertEqual(10, microarray_samples.count())
        downloader_jobs = DownloaderJob.objects.all()
        self.assertEqual(10, downloader_jobs.count())

        # And 18 of which are RNA-Seq so they won't have downloader jobs.
        rna_seq_samples = Sample.objects.filter(technology="RNA-SEQ")
        self.assertEqual(18, rna_seq_samples.count())

        # Make sure there aren't extra OriginalFiles
        original_files = OriginalFile.objects.all()
        self.assertEqual(10, original_files.count())

    @vcr.use_cassette("/home/user/data_store/cassettes/surveyor.geo.get_pubmet_id_title.yaml")
    def test_get_pubmed_id_title(self):
        """ We scrape PMIDs now. """
        resp = get_title_and_authors_for_pubmed_id("22367537")
        self.assertEqual(
            resp[0],
            "Sequencing of neuroblastoma identifies chromothripsis and defects in neuritogenesis genes.",
        )
        self.assertEqual(
            resp[1],
            [
                "Molenaar JJ",
                "Koster J",
                "Zwijnenburg DA",
                "van Sluis P",
                "Valentijn LJ",
                "van der Ploeg I",
                "Hamdi M",
                "van Nes J",
                "Westerman BA",
                "van Arkel J",
                "Ebus ME",
                "Haneveld F",
                "Lakeman A",
                "Schild L",
                "Molenaar P",
                "Stroeken P",
                "van Noesel MM",
                "Ora I",
                "Santo EE",
                "Caron HN",
                "Westerhout EM",
                "Versteeg R",
            ],
        )
