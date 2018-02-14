from __future__ import absolute_import, unicode_literals
import urllib.request
import os
import shutil
import zipfile
from typing import List
from contextlib import closing
from data_refinery_common.models import File, DownloaderJob
from data_refinery_common.models.new_models import Experiment, Sample, ExperimentAnnotation, ExperimentSampleAssociation, OriginalFile, DownloaderJobOriginalFileAssociation
from data_refinery_workers.downloaders import utils
from data_refinery_common.logging import get_and_configure_logger
from data_refinery_common.utils import get_env_variable


logger = get_and_configure_logger(__name__)
LOCAL_ROOT_DIR = get_env_variable("LOCAL_ROOT_DIR", "/home/user/data_store")


# chunk_size is in bytes
CHUNK_SIZE = 1024 * 256


def _verify_batch_grouping(files: List[File], job: DownloaderJob) -> None:
    """All batches in the same job should have the same downloader url"""
    for file in files:
        if file.download_url != files[0].download_url:
            failure_message = ("A Batch's file doesn't have the same download "
                               "URL as the other batches' files.")
            logger.error(failure_message,
                         downloader_job=job.id)
            job.failure_reason = failure_message
            raise ValueError(failure_message)


def _download_file(download_url: str, file_path: str, job: DownloaderJob) -> None:
    """ Download a file from ArrayExpress via FTP. There is no Aspera endpoint
    which I can find. """
    try:
        logger.debug("Downloading file from %s to %s.",
                     download_url,
                     file_path,
                     downloader_job=job.id)
        target_file = open(file_path, "wb")
        with closing(urllib.request.urlopen(download_url)) as request:
            shutil.copyfileobj(request, target_file, CHUNK_SIZE)
    except Exception:
        logger.exception("Exception caught while downloading batch.",
                         downloader_job=job.id)
        job.failure_reason = "Exception caught while downloading batch"
        raise
    finally:
        target_file.close()


# def _extract_files(files: List[File], job: DownloaderJob) -> None:
#     """Extract zip from temp directory and move to raw directory.

#     Additionally this function sets the size_in_bytes field of each
#     Batch in batches. To save database calls it does not save the
#     batch itself since it will be saved soon when its status
#     changes in utils.end_job.
#     """
#     # zip_path and local_dir should be common to all batches in the group
#     job_dir = utils.JOB_DIR_PREFIX + str(job.id)
#     zip_path = files[0].get_temp_download_path(job_dir)
#     local_dir = files[0].get_temp_dir(job_dir)
#     dirs_to_clean = set()

#     logger.debug("Extracting %s", zip_path, downloader_job=job.id)

#     try:
#         zip_ref = zipfile.ZipFile(zip_path, "r")
#         zip_ref.extractall(local_dir)

#         for file in files:
#             batch_directory = file.get_temp_dir(job_dir)
#             raw_file_location = file.get_temp_pre_path(job_dir)

#             # The platform is part of the batch's location so if the
#             # batches in this job have different platforms then some
#             # of them need to be moved to the directory corresponding
#             # to thier platform.
#             if local_dir != batch_directory:
#                 os.makedirs(batch_directory, exist_ok=True)
#                 dirs_to_clean.add(batch_directory)
#                 incorrect_location = os.path.join(local_dir, file.name)
#                 os.rename(incorrect_location, raw_file_location)

#             file.size_in_bytes = os.path.getsize(raw_file_location)
#             file.save()
#             file.upload_raw_file(job_dir)
#     except Exception:
#         logger.exception("Exception caught while extracting %s",
#                          zip_path,
#                          downloader_job=job.id)
#         job.failure_reason = "Exception caught while extracting " + zip_path
#         raise
#     finally:
#         zip_ref.close()
#         file.remove_temp_directory(job_dir)
#         for directory in dirs_to_clean:
#             shutil.rmtree(directory)

def _extract_files(file_path: str, accession_code: str) -> List[str]:
    """Extract zip from temp directory and move to raw directory.

    Additionally this function sets the size_in_bytes field of each
    Batch in batches. To save database calls it does not save the
    batch itself since it will be saved soon when its status
    changes in utils.end_job.
    """
    # zip_path and local_dir should be common to all batches in the group
    # job_dir = utils.JOB_DIR_PREFIX + str(job.id)
    # zip_path = files[0].get_temp_download_path(job_dir)
    # local_dir = files[0].get_temp_dir(job_dir)
    # dirs_to_clean = set()

    logger.debug("Extracting %s!", file_path)

    print(file_path)

    try:
        # This is technically an unsafe operation.
        # However, we're trusting AE as a data source.
        zip_ref = zipfile.ZipFile(file_path, "r")

        # TODO: Make this an absolute path
        abs_with_code_raw = LOCAL_ROOT_DIR + '/' + accession_code + '/raw/'
        zip_ref.extractall(abs_with_code_raw)
        zip_ref.close()

        # os.abspath doesn't do what I thought it does, hency this monstrocity.
        files = [{'absolute_path': abs_with_code_raw + f, 'filename': f} for f in os.listdir(abs_with_code_raw)]

        # for file in files:
        #     batch_directory = file.get_temp_dir(job_dir)
        #     raw_file_location = file.get_temp_pre_path(job_dir)

        #     # The platform is part of the batch's location so if the
        #     # batches in this job have different platforms then some
        #     # of them need to be moved to the directory corresponding
        #     # to thier platform.
        #     if local_dir != batch_directory:
        #         os.makedirs(batch_directory, exist_ok=True)
        #         dirs_to_clean.add(batch_directory)
        #         incorrect_location = os.path.join(local_dir, file.name)
        #         os.rename(incorrect_location, raw_file_location)

        #     file.size_in_bytes = os.path.getsize(raw_file_location)
        #     file.save()
        #     file.upload_raw_file(job_dir)
    except Exception as e:
        print(e)
        reason = "Exception %s caught while extracting %s", str(e), zip_path
        logger.exception(reason)
        job.failure_reason = reason
        raise
    # finally:
    #     zip_ref.close()
    #     for directory in dirs_to_clean:
    #         shutil.rmtree(directory)

    return files

def download_array_express(job_id: int) -> None:
    """The main function for the Array Express Downloader.

    Downloads a single zip file containing the .PCL files representing
    samples relating to a single experiement stored in
    ArrayExpress. Each of these files is a separate Batch, so the file
    is unzipped and then each Batch's data is stored in Temporary
    Storage.
    """
    job = utils.start_job(job_id)

    file_assocs = DownloaderJobOriginalFileAssociation.objects.filter(downloader_job=job)
    original_file = file_assocs[0].original_file # AE should never have more than one zip, but we can iterate here if we discover this is false.
    # if original_file.is_downloaded:
    #     logger.info("This file is already downloaded!")
    #     return
    url = original_file.source_url
    accession_code = job.accession_code

    #success = True
    
    # if batches.count() > 0:
    #     files = File.objects.filter(batch__in=batches)
    #     target_directory = files[0].get_temp_dir(job_dir)
    #     os.makedirs(target_directory, exist_ok=True)
    #     target_file_path = files[0].get_temp_download_path(job_dir)
    #     download_url = files[0].download_url
    # else:
    #     logger.error("No batches found.",
    #                  downloader_job=job_id)
    #     success = False
    #if success:

    # There is going to be a prettier way of doing this
    # relations = ExperimentSampleAssociation.objects.filter(experiment=experiment)
    # samples = Sample.objects.filter(id__in=relations.values('sample_id'))
    # urls = list(samples.values_list("originalfile__source_archive_url", flat=True).distinct())

    # for sample in samples:
    #     print(sample.source_filename)

    # First, get all the unique sample archive URLs.
    # There may be more than one!
    # Then, unpack all the ones downloaded.
    # Then create processor jobs!


    og_files = []
    try:
        # The files for all of the samples are
        # contained within the same zip file. Therefore only
        # download the one.
        os.makedirs(LOCAL_ROOT_DIR + '/' + accession_code, exist_ok=True)
        dl_file_path = LOCAL_ROOT_DIR + '/' + accession_code + '/' + accession_code + ".zip"
        _download_file(url, dl_file_path, job)

        extracted_files = _extract_files(dl_file_path, accession_code)

        for og_file in extracted_files:
            # TODO: We _should_ be able to use GET here - anything more than 1 sample per
            # filename is a problem. However, I need to know more about the naming convention.
            try:
                original_file = OriginalFile.objects.filter(source_filename=og_file['filename']).order_by('created_at')[0]
                original_file.is_downloaded=True
                original_file.is_archive=False
                original_file.source_absolute_file_path = og_file['absolute_path']
                original_file.save()
                og_files.append(original_file)
            except Exception:
                logger.debug("Found a file we didn't have an OriginalFile for! Why did this happen?: " + og_file['filename'])
        success=True
    except Exception as e:
        print(e)
        # Exceptions are already logged and handled.
        # Just need to mark the job as failed.
        success = False
        raise

    if success:
        logger.debug("File downloaded and extracted successfully.",
                     url,
                     downloader_job=job_id)

    utils.end_downloader_job(job, success)

    if success:
        utils.create_processor_jobs_for_original_files(og_files)
