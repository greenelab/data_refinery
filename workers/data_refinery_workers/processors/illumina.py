import csv
import multiprocessing
import os
import re
import subprocess
import tempfile
from typing import Dict

from django.utils import timezone

import numpy as np
import pandas as pd

from data_refinery_common.enums import PipelineEnum, ProcessorPipeline
from data_refinery_common.logging import get_and_configure_logger
from data_refinery_common.message_queue import send_job
from data_refinery_common.models import (
    ComputationalResult,
    ComputedFile,
    Pipeline,
    ProcessorJob,
    ProcessorJobOriginalFileAssociation,
    Sample,
    SampleAnnotation,
    SampleComputedFileAssociation,
    SampleResultAssociation,
)
from data_refinery_common.utils import get_env_variable
from data_refinery_workers.processors import utils

S3_BUCKET_NAME = get_env_variable("S3_BUCKET_NAME", "data-refinery")
LOCAL_ROOT_DIR = get_env_variable("LOCAL_ROOT_DIR", "/home/user/data_store")
logger = get_and_configure_logger(__name__)


def _prepare_files(job_context: Dict) -> Dict:
    """Adds the keys "sanitized_file_path" and "output_file_path" to
    job_context so everything is prepared for processing.
    """
    # All files for the job are in the same directory.
    job_context["work_dir"] = (
        LOCAL_ROOT_DIR + "/" + "processor_job_" + str(job_context["job_id"]) + "/"
    )
    os.makedirs(job_context["work_dir"], exist_ok=True)

    original_file = job_context["original_files"][0]
    job_context["input_file_path"] = original_file.absolute_file_path

    # This should not happen, but if it does I would rather know about it here,
    # whereas before we would get random failures later down the pipeline
    if not job_context["input_file_path"].endswith(".txt"):
        logger.error(
            "Input file doesn't have a suffix we recognize, probably an invalid format",
            input_file=original_file.absolute_file_path,
        )
        job_context["job"].failure_reason = "Couldn't recognize the input file format"
        job_context["success"] = False
        job_context["job"].no_retry = True
        return job_context

    sanitized_filename = job_context["input_file_path"].split("/")[-1] + ".sanitized"
    job_context["sanitized_file_path"] = job_context["work_dir"] + sanitized_filename

    new_filename = original_file.absolute_file_path.split("/")[-1].replace(".txt", ".PCL")
    job_context["output_file_path"] = job_context["work_dir"] + new_filename

    return job_context


def _detect_encoding(job_context: Dict) -> Dict:
    """Some Illumina files are not encoded using utf-8, so we need to use
    `file` to detect their encoding"""

    try:
        encoding = subprocess.check_output(
            ["file", "--brief", "--mime-encoding", job_context["input_file_path"],],
            encoding="utf-8",
        ).strip()
    except subprocess.CalledProcessError as e:
        logger.exception(
            "Failed to detect the input file's encoding",
            processor_job=job_context["job_id"],
            input_file=job_context["input_file_path"],
        )
        job_context["job"].failure_reason = "Failed to detect the input file's encoding"
        job_context["success"] = False
        job_context["job"].no_retry = True

    if encoding not in ["us-ascii", "utf-8", "iso-8859-1"]:
        logger.error(
            "Input file has unrecognized encoding",
            input_file=job_context["input_file_path"],
            encoding=encoding,
        )
        job_context["job"].failure_reason = f"Input file has unrecognized encoding {encoding}"
        job_context["success"] = False
        job_context["job"].no_retry = True

    job_context["encoding"] = encoding

    return job_context


def _sanitize_input_file(job_context: Dict) -> Dict:
    """Remove all of the SOFT-specific extensions in the original file (see
    https://www.ncbi.nlm.nih.gov/geo/info/soft.html) plus some extra things we
    found that make R choke.  Also, some files aren't utf-8 encoded, so we will
    re-encode them into utf-8."""

    with open(job_context["input_file_path"], "r", encoding=job_context["encoding"]) as file_input:
        with open(job_context["sanitized_file_path"], "w", encoding="utf-8") as file_output:
            for line in file_input:
                HEADER_CHARS = ["#", "!", "^"]

                # Sometimes we have a quoted header, so we need to check both
                is_header = line[0] in HEADER_CHARS or (
                    line[0] in ["'", '"'] and line[1] in HEADER_CHARS
                )
                is_empty = line.strip() == ""

                # There are some weird lines that start with accession
                # codes that don't hold gene measurements
                is_accession_line = line[0:3].upper() == "GSM"

                if not is_header and not is_empty and not is_accession_line:
                    file_output.write(line)
                    wrote_a_line = True

    if not wrote_a_line:
        logger.error(
            "Filtered every line out of the input file", input_file=job_context["input_file_path"]
        )
        job_context["job"].failure_reason = "No valid rows detected in the input file"
        job_context["success"] = False
        job_context["job"].no_retry = True

    return job_context


def _convert_sanitized_to_tsv(job_context: Dict) -> Dict:
    """Now that we have removed all of the SOFT-specific extensions, we are left
    with some kind of csv/tsv/ssv that may or may not have quoted strings. To
    make things easier to parse in R, we will try to sniff these features and
    output a uniform format for the R code to read"""

    _, tmpfile = tempfile.mkstemp(
        suffix=".txt", dir=os.path.dirname(job_context["sanitized_file_path"])
    )
    with open(job_context["sanitized_file_path"], "r") as file_input:
        with open(tmpfile, "w") as file_output:
            dialect = csv.Sniffer().sniff(file_input.read(16384), delimiters="\t ,")
            file_input.seek(0)
            reader = csv.reader(file_input, dialect=dialect)
            writer = csv.writer(file_output, delimiter="\t")

            reader_iter = iter(reader)

            headers = next(reader)
            first_content = next(reader)

            # Sometimes input files have one less header than they have rows,
            # which means that we should interpret the first column as ID_REF.
            # The rest of our code expects this explicit header, though, so we
            # will insert it here.
            if len(headers) == len(first_content) - 1:
                headers = ["ID_REF", *headers]

            writer.writerow(headers)
            writer.writerow(first_content)

            for row in reader_iter:
                writer.writerow(row)

    os.rename(tmpfile, job_context["sanitized_file_path"])

    return job_context


def _detect_columns(job_context: Dict) -> Dict:
    """Detect which columns match to which inputs.

    Related: https://github.com/AlexsLemonade/refinebio/issues/86#issuecomment-379308817

    We need to find:

        First column should be ID_REF or PROBE_ID and the type should be string.
        Detection Pval column
        Expression column (contains sample title and NOT 'BEAD')

        Header examples:
            ['ID_REF', 'LV-C&si-Control-1', 'Detection Pval',
            'LV-C&si-Control-2', 'Detection Pval', 'LV-C&si-Control-3', 'Detection
            Pval', 'LV-C&si-EZH2-1', 'Detection Pval', 'LV-C&si-EZH2-2', 'Detection
            Pval', 'LV-C&si-EZH2-3', 'Detection Pval', 'LV-EZH2&si-EZH2-1',
            'Detection Pval', 'LV-EZH2&si-EZH2-2', 'Detection Pval', 'LV-EZH2&si-
            EZH2-3', 'Detection Pval', 'LV-T350A&si-EZH2-1', 'Detection Pval', 'LV-
            T350A&si-EZH2-2', 'Detection Pval', 'LV-T350A&si-EZH2-3', 'Detection
            Pval']

    Adds the following keys to job_context:
        columnIds: the identifiers of columns which contain expression data
        probeId: which is the value of the column containing the probe identifiers.
        detectionPval: a string which identifies Pvalue columns
    """
    try:
        input_file = job_context["sanitized_file_path"]
        headers = None
        with open(input_file, "r") as tsv_in:
            tsv_in = csv.reader(tsv_in, delimiter="\t")
            for row in tsv_in:
                headers = row
                break

        # Ex GSE45331_non-normalized.txt
        predicted_header = 0

        # Some files start with blank columns, so let's skip past those
        while headers[predicted_header] == "":
            predicted_header += 1

        if headers[predicted_header].upper() in ["TARGETID", "TARGET_ID"]:
            predicted_header += 1

        # First the probe ID column
        if headers[predicted_header].upper() == "ILLUMICODE":
            logger.error(
                "Tried to process a beadTypeFile.txt, which we don't support",
                input_file=job_context["sanitized_file_path"],
            )
            job_context["job"].failure_reason = "Unsupported filetype 'beadTypeFile.txt'"
            job_context["success"] = False
            job_context["job"].no_retry = True
            return job_context
        elif headers[predicted_header].upper() not in [
            "ID_REF",
            "PROBE_ID",
            "IDREF",
            "PROBEID",
            "REF_ID",
            "REFID",
            "IDPROBE",
            "ID_PROBE",
        ]:
            job_context["job"].failure_reason = (
                "Could not find any ID column in headers "
                + str(headers)
                + " for file "
                + job_context["sanitized_file_path"]
            )
            job_context["success"] = False
            return job_context
        else:
            job_context["probeId"] = headers[predicted_header]

        # Then check to make sure a detection pvalue exists, which is always(?) some form of
        # 'Detection Pval'
        for header in headers:
            # check if header contains something like "detection pval" or "detection_pval"
            pvalue_header = re.search(r"(detection)([\W_]?)(pval\w*)", header, re.IGNORECASE)
            if pvalue_header:
                break
        else:
            job_context["job"].failure_reason = "Could not detect PValue column!"
            job_context["success"] = False
            job_context["job"].no_retry = True
            return job_context

        # Then, finally, create an absolutely bonkers regular expression
        # which will explicitly hit on any sample which contains a sample
        # ID _and_ ignores the magical word 'BEAD', etc. Great!
        column_ids = set()
        for sample in job_context["samples"]:
            for offset, header in enumerate(headers, start=1):

                if sample.title == header:
                    column_ids.add(offset)
                    continue

                # Sometimes the title might actually be in the description field.
                # To find this, look in all the related SampleAnnotations.
                # Since there are multiple annotations, we need to break early before continuing.
                # Related: https://github.com/AlexsLemonade/refinebio/issues/499
                continue_me = False
                for annotation in sample.sampleannotation_set.filter(is_ccdl=False):
                    try:
                        if annotation.data.get("description", "")[0] == header:
                            column_ids.add(offset)
                            continue_me = True
                            break
                    except Exception:
                        pass
                if continue_me:
                    # Treat the header as the real title, as we will need it later.
                    sample.title = header
                    sample.save()
                    continue

                if header.upper().replace(" ", "_") == "RAW_VALUE":
                    column_ids.add(offset)
                    continue

                if (
                    header != ""
                    and (
                        sample.title.upper() in header.upper()
                        or sample.title.upper().endswith("_" + header.upper())
                    )
                    and "BEAD" not in header.upper()
                    and "NARRAYS" not in header.upper()
                    and "ARRAY_STDEV" not in header.upper()
                    and "PVAL" not in header.upper().replace(" ", "").replace("_", "")
                ):
                    column_ids.add(offset)
                    continue

        for offset, header in enumerate(headers, start=1):
            if "AVG_Signal" in header:
                column_ids.add(offset)
                continue

        if len(column_ids) == 0:
            job_context[
                "job"
            ].failure_reason = f"could not find columns ids in {job_context['sanitized_file_path']}"
            job_context["success"] = False
            logger.error("Could not find columns ids in " + job_context["sanitized_file_path"])
            job_context["job"].no_retry = True
            return job_context

        job_context["columnIds"] = ",".join(map(lambda id: str(id), column_ids))
    except Exception as e:
        job_context[
            "job"
        ].failure_reason = (
            f"failure to extract columns in {job_context['sanitized_file_path']}: {e}"
        )
        job_context["success"] = False
        logger.exception(
            "Failed to extract columns in " + job_context["sanitized_file_path"], exception=str(e)
        )
        job_context["job"].no_retry = True
        return job_context

    return job_context


def _detect_platform(job_context: Dict) -> Dict:
    """
    Determine the platform/database to process this sample with.
    They often provide something like "V2" or "V 2", but we don't trust them so we detect it ourselves.

    Related: https://github.com/AlexsLemonade/refinebio/issues/232
    """

    all_databases = {
        "HOMO_SAPIENS": [
            "illuminaHumanv1",
            "illuminaHumanv2",
            "illuminaHumanv3",
            "illuminaHumanv4",
        ],
        "MUS_MUSCULUS": ["illuminaMousev1", "illuminaMousev1p1", "illuminaMousev2",],
        "RATTUS_NORVEGICUS": ["illuminaRatv1"],
    }

    sample0 = job_context["samples"][0]
    databases = all_databases[sample0.organism.name]

    # Loop over all of the possible platforms and find the one with the best match.
    highest = 0.0
    high_mapped_percent = 0.0
    high_db = None
    for platform in databases:
        try:
            result = subprocess.check_output(
                [
                    "/usr/bin/Rscript",
                    "--vanilla",
                    "/home/user/data_refinery_workers/processors/detect_database.R",
                    "--platform",
                    platform,
                    "--inputFile",
                    job_context["sanitized_file_path"],
                    "--column",
                    job_context["probeId"],
                ]
            )

            results = result.decode().split("\n")
            cleaned_result = float(results[0].strip())

            if cleaned_result > highest:
                highest = cleaned_result
                high_db = platform
                high_mapped_percent = float(results[1].strip())

        except Exception as e:
            logger.exception(e, processor_job_id=job_context["job"].id)
            continue

    # Record our sample detection outputs for every sample.
    for sample in job_context["samples"]:
        sa = SampleAnnotation()
        sa.sample = sample
        sa.is_ccdl = True
        sa.data = {
            "detected_platform": high_db,
            "detection_percentage": highest,
            "mapped_percentage": high_mapped_percent,
        }
        sa.save()

    # If the match is over 75%, record this and process it on that platform.
    if high_mapped_percent > 75.0:
        job_context["platform"] = high_db
    # The match percentage is too low - send this to the no-opper instead.
    else:

        logger.info("Match percentage too low, NO_OP'ing and aborting.", job=job_context["job_id"])

        processor_job = ProcessorJob()
        processor_job.downloader_job = job_context["job"].downloader_job
        processor_job.pipeline_applied = "NO_OP"
        processor_job.ram_amount = job_context["job"].ram_amount
        processor_job.save()

        assoc = ProcessorJobOriginalFileAssociation()
        assoc.original_file = job_context["original_files"][0]
        assoc.processor_job = processor_job
        assoc.save()

        try:
            send_job(ProcessorPipeline.NO_OP, processor_job)
        except Exception as e:
            # Batch dispatch error, likely during local test.
            logger.error(e, job=processor_job)

        job_context["abort"] = True

    return job_context


def _run_illumina(job_context: Dict) -> Dict:
    """Processes an input TXT file to an output PCL file using a custom R script.

    Expects a job_context which has been pre-populated with inputs, outputs
    and the column identifiers which the R script needs for processing.
    """

    try:
        job_context["time_start"] = timezone.now()

        formatted_command = [
            "/usr/bin/Rscript",
            "--vanilla",
            "/home/user/data_refinery_workers/processors/illumina.R",
            "--probeId",
            job_context["probeId"],
            "--expression",
            job_context["columnIds"],
            "--platform",
            job_context["platform"],
            "--inputFile",
            job_context["sanitized_file_path"],
            "--outputFile",
            job_context["output_file_path"],
            "--cores",
            str(multiprocessing.cpu_count()),
        ]

        output = subprocess.check_output(formatted_command).decode()
        logger.debug(f"illumina.R ran successfully with output '{output}'")

        job_context["formatted_command"] = " ".join(formatted_command)

        job_context["time_end"] = timezone.now()

    except subprocess.CalledProcessError as e:
        error_template = (
            "Encountered error in R code while running illumina.R"
            " pipeline during processing of {0}: {1}"
        )
        error_message = error_template.format(job_context["sanitized_file_path"], str(e))
        logger.error(error_message, processor_job=job_context["job_id"])
        logger.debug(f"illumina.R failed with output '{e.output.decode()}'")
        job_context["job"].failure_reason = error_message
        job_context["success"] = False

    except Exception as e:
        logger.exception(
            "Exception raised while running illumina.R", processor_job=job_context["job_id"]
        )
        job_context["job"].failure_reason = f"Exception raised while running illumina.R: '{e}'"
        job_context["success"] = False

    return job_context


def _get_sample_for_column(column: str, job_context: Dict) -> Sample:
    # First of all check if the title is the column name
    try:
        return job_context["samples"].get(title=column)
    except Sample.DoesNotExist:
        pass
    # Or maybe they named their samples with a common prefix
    try:
        return job_context["samples"].get(title__iendswith="_" + column)
    except Sample.DoesNotExist:
        pass

    # If the column name is not the title, maybe they used the convention
    # <SAMPLE_TITLE>(.AVG)?_Signal
    title_match = re.match(r"(?P<title>.*?)(\.AVG)?_Signal", column)
    if title_match is not None:
        try:
            return job_context["samples"].get(title=title_match.group("title"))
        except Sample.DoesNotExist:
            pass

    # Or maybe they also have a named detection pvalue column using the same
    # naming scheme
    name_match = re.match(r"(?P<name>.*)\.AVG_Signal", column)
    if name_match is not None:
        try:
            return job_context["samples"].get(
                sampleannotation__data__geo_columns__contains="{}.Detection Pval".format(
                    name_match.group("name")
                )
            )
        except Sample.DoesNotExist:
            pass

    return None


def _create_result_objects(job_context: Dict) -> Dict:
    """Create the ComputationalResult objects after a Scan run is complete"""

    result = ComputationalResult()
    result.commands.append(job_context["formatted_command"])
    result.is_ccdl = True
    result.is_public = True
    result.time_start = job_context["time_start"]
    result.time_end = job_context["time_end"]
    try:
        processor_key = "ILLUMINA_SCAN"
        result.processor = utils.find_processor(processor_key)
    except Exception as e:
        return utils.handle_processor_exception(job_context, processor_key, e)

    result.save()
    job_context["pipeline"].steps.append(result.id)

    # Split the result into smashable subfiles
    big_tsv = job_context["output_file_path"]
    data = pd.read_csv(big_tsv, sep="\t", header=0, index_col=0)
    individual_files = []
    frames = np.split(data, len(data.columns), axis=1)
    for frame in frames:
        filename = (
            frame.columns.values[0].replace("&", "").replace("*", "").replace(";", "") + ".tsv"
        )
        frame_path = job_context["work_dir"] + filename
        frame.to_csv(frame_path, sep="\t", encoding="utf-8")

        # This needs to be the same as the ones in the job context!
        sample = _get_sample_for_column(frame.columns.values[0], job_context)
        if sample is None:
            job_context["job"].failure_reason = (
                "Could not find sample for column "
                + frame.columns.values[0]
                + " while splitting Illumina file "
                + big_tsv
            )
            job_context["success"] = False
            job_context["job"].no_retry = True
            return job_context

        computed_file = ComputedFile()
        computed_file.absolute_file_path = frame_path
        computed_file.filename = frame_path.split("/")[-1]
        computed_file.result = result
        computed_file.is_smashable = True
        computed_file.is_qc = False
        computed_file.is_public = True
        computed_file.calculate_sha1()
        computed_file.calculate_size()
        computed_file.save()
        job_context["computed_files"].append(computed_file)

        SampleResultAssociation.objects.get_or_create(sample=sample, result=result)

        SampleComputedFileAssociation.objects.get_or_create(
            sample=sample, computed_file=computed_file
        )

        individual_files.append(computed_file)

    logger.debug("Created %s", result)
    job_context["success"] = True
    job_context["individual_files"] = individual_files
    job_context["result"] = result

    return job_context


def illumina_to_pcl(job_id: int, cleanup=None) -> None:
    pipeline = Pipeline(name=PipelineEnum.ILLUMINA.value)
    initial_job_context = {"job_id": job_id, "pipeline": pipeline}

    # When running the tests, don't clean up original files so we don't have to
    # keep downloading them.
    if cleanup is not None:
        initial_job_context["cleanup"] = cleanup

    return utils.run_pipeline(
        initial_job_context,
        [
            utils.start_job,
            _prepare_files,
            _detect_encoding,
            _sanitize_input_file,
            _convert_sanitized_to_tsv,
            _detect_columns,
            _detect_platform,
            _run_illumina,
            _create_result_objects,
            utils.end_job,
        ],
    )
