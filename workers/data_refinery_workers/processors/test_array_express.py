import os
import shutil
from django.test import TestCase
from unittest.mock import patch, MagicMock
from data_refinery_models.models import (
    SurveyJob,
    Batch,
    BatchStatuses,
    ProcessorJob,
)
from data_refinery_workers.processors import array_express, utils
from data_refinery_common import file_management


def init_batch():
    survey_job = SurveyJob(source_type="ARRAY_EXPRESS")
    survey_job.save()

    batch = Batch(
        survey_job=survey_job,
        source_type="ARRAY_EXPRESS",
        size_in_bytes=0,
        download_url="ftp://ftp.ebi.ac.uk/pub/databases/microarray/data/experiment/GEOD/E-GEOD-59071/E-GEOD-59071.raw.3.zip/GSM1426072_CD_colon_active_2.CEL",  # noqa
        raw_format="CEL",
        processed_format="PCL",
        pipeline_required="AFFY_TO_PCL",
        platform_accession_code="A-AFFY-1",
        experiment_accession_code="E-MTAB-3050",
        experiment_title="It doesn't really matter.",
        name="CE1234.CEL",
        internal_location="A-AFFY-1/AFFY_TO_PCL/",
        organism_id=9606,
        organism_name="HOMO SAPIENS",
        release_date="2017-05-05",
        last_uploaded_date="2017-05-05",
        status=BatchStatuses.DOWNLOADED.value
    )
    batch.save()
    return batch


class PrepareFilesTestCase(TestCase):
    def test_success(self):
        batch = init_batch()

        processor_job = ProcessorJob.create_job_and_relationships(batches=[batch])
        os.makedirs(file_management.get_raw_dir(batch), exist_ok=True)
        raw_path = file_management.get_raw_path(batch)
        with open(raw_path, "w") as dummy_pcl:
            dummy_pcl.write("This is a dummy file for tests to operate upon.")

        job_context = utils.start_job({"job": processor_job})
        job_context = array_express._prepare_files(job_context)

        input_file = job_context["input_file"]
        self.assertIsInstance(input_file, str)
        self.assertIsInstance(job_context["output_file"], str)

        self.assertTrue(os.path.isfile(input_file))
        os.remove(raw_path)
        os.remove(input_file)

    def test_failure(self):
        batch = init_batch()

        processor_job = ProcessorJob.create_job_and_relationships(batches=[batch])

        job_context = utils.start_job({"job": processor_job,
                                       "job_id": processor_job.id})
        job_context = array_express._prepare_files(job_context)

        self.assertFalse(job_context["success"])
        self.assertEqual(processor_job.failure_reason,
                         "Exception caught while retrieving raw file CE1234.CEL")

        self.assertFalse(os.path.isfile(file_management.get_temp_pre_path(batch)))


class DetermineBrainarrayPackageTestCase(TestCase):
    @patch("data_refinery_workers.processors.array_express.file_management")
    def test_success(self, mock_file_management: MagicMock):
        mock_file_management.remove_temp_directory = MagicMock()

        batch = init_batch()
        batch.platform_accession_code = "TEST"
        batch.internal_location = "TEST/AFFY_TO_PCL"
        batch.name = "GSM1426186_UC_colon_inactive_201.CEL"
        batch.save()

        processor_job = ProcessorJob.create_job_and_relationships(batches=[batch])

        # We have a test file in the repo, but it needs to be in the
        # correct location which depends on the ID of the Batch, which
        # changes based on the order tests are run in.
        test_file_path = "/home/user/data_store/temp/TEST/AFFY_TO_PCL/" + batch.name
        input_file = file_management.get_temp_pre_path(batch)
        os.makedirs(file_management.get_temp_dir(batch), exist_ok=True)
        shutil.copyfile(test_file_path, input_file)

        job_context = {"job_id": processor_job.id,
                       "job": processor_job,
                       "input_file": input_file}

        job_context = array_express._determine_brainarray_package(job_context)

        self.assertEqual(job_context["brainarray_package"], "hugene10sthsentrezgprobe")
        mock_file_management.remove_temp_directory.assert_not_called()

        # Clean up the copied file
        os.remove(input_file)

    def test_failure(self):
        batch = init_batch()
        batch.platform_accession_code = "TEST2"
        batch.internal_location = "TEST2/AFFY_TO_PCL"
        batch.name = "dummy"
        batch.save()
        processor_job = ProcessorJob.create_job_and_relationships(batches=[batch])
        os.makedirs(file_management.get_temp_dir(batch), exist_ok=True)
        with open(file_management.get_temp_pre_path(batch), "w") as dummy_pcl:
            dummy_pcl.write("This is a dummy file for tests to operate upon.")

        input_file = file_management.get_temp_pre_path(batch)
        job_context = {"job_id": processor_job.id,
                       "job": processor_job,
                       "batches": [batch],
                       "input_file": input_file}

        job_context = array_express._determine_brainarray_package(job_context)
        failure_reason_template = """unable to read Affy header in input file /home/user/data_store/temp/TEST2/AFFY_TO_PCL/batch_{0}/dummy due to error: Error in (function (filename, info = c("basic", "full"), verbose = FALSE)  : \n  Is /home/user/data_store/temp/TEST2/AFFY_TO_PCL/batch_{1}/dummy really a CEL file? tried reading as text, gzipped text, binary, gzipped binary, command console and gzipped command console formats\n"""  # noqa

        self.assertFalse("brainarray_package" in job_context)
        self.assertFalse(job_context["success"])
        self.assertFalse(os.path.isfile(input_file))
        self.assertEqual(processor_job.failure_reason,
                         failure_reason_template.format(batch.id, batch.id))


class RunScanUPCTestCase(TestCase):
    @patch("data_refinery_workers.processors.array_express.file_management")
    def test_success(self, mock_file_management: MagicMock):
        mock_file_management.remove_temp_directory = MagicMock()

        batch = init_batch()
        batch.platform_accession_code = "TEST"
        batch.internal_location = "TEST/AFFY_TO_PCL"
        batch.name = "GSM1426186_UC_colon_inactive_201.CEL"
        processor_job = ProcessorJob.create_job_and_relationships(batches=[batch])

        # We have a test file in the repo, but it needs to be in the
        # correct location which depends on the ID of the Batch, which
        # changes based on the order tests are run in.
        test_file_path = "/home/user/data_store/temp/TEST/AFFY_TO_PCL/" + batch.name
        input_file = file_management.get_temp_pre_path(batch)
        os.makedirs(file_management.get_temp_dir(batch), exist_ok=True)
        shutil.copyfile(test_file_path, input_file)

        output_file = file_management.get_temp_post_path(batch)
        job_context = {"job_id": processor_job.id,
                       "job": processor_job,
                       "batches": [batch],
                       "brainarray_package": "hugene10sthsentrezgprobe",
                       "input_file": input_file,
                       "output_file": output_file}

        # If this file already exists for any reason then we aren't
        # actually testing that it is generated
        self.assertFalse(os.path.isfile(output_file))

        job_context = array_express._run_scan_upc(job_context)

        # success is only populated by this function on an error
        self.assertFalse("success" in job_context)
        self.assertTrue(os.path.isfile(output_file))

        # Clean up the processed file
        os.remove(output_file)

        # Clean up the copied file
        os.remove(input_file)

    def test_failure(self):
        batch = init_batch()
        batch.platform_accession_code = "TEST2"
        batch.internal_location = "TEST2/AFFY_TO_PCL"
        batch.name = "dummy"
        batch.save()
        processor_job = ProcessorJob.create_job_and_relationships(batches=[batch])
        os.makedirs(file_management.get_temp_dir(batch), exist_ok=True)
        with open(file_management.get_temp_pre_path(batch), "w") as dummy_pcl:
            dummy_pcl.write("This is a dummy file for tests to operate upon.")

        input_file = file_management.get_temp_pre_path(batch)
        output_file = file_management.get_temp_post_path(batch)
        job_context = {"job_id": processor_job.id,
                       "job": processor_job,
                       "batches": [batch],
                       "brainarray_package": "hugene10sthsentrezgprobe",
                       "input_file": input_file,
                       "output_file": output_file}

        job_context = array_express._run_scan_upc(job_context)
        failure_reason_template = """encountered error in R code while processing /home/user/data_store/temp/TEST2/AFFY_TO_PCL/batch_{0}/dummy: Error in {{ : \n  task 1 failed - "Is /home/user/data_store/temp/TEST2/AFFY_TO_PCL/batch_{1}/dummy really a CEL file? tried reading as text, gzipped text, binary, gzipped binary, command console and gzipped command console formats\n"\n"""  # noqa

        self.assertFalse(job_context["success"])
        self.assertFalse(os.path.isfile(input_file))
        self.assertFalse(os.path.isfile(output_file))
        self.assertEqual(processor_job.failure_reason,
                         failure_reason_template.format(batch.id, batch.id))
