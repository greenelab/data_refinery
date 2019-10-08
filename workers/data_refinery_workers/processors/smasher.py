# -*- coding: utf-8 -*-

import boto3
import csv
import os
import rpy2
import rpy2.robjects as ro
import shutil
import simplejson as json
import string
import warnings
import requests
import psutil
import multiprocessing
import logging
import time

from botocore.exceptions import ClientError
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from pathlib import Path
from rpy2.robjects import pandas2ri
from rpy2.robjects import r as rlang
from rpy2.robjects.packages import importr
from sklearn import preprocessing
from typing import Dict, List
import numpy as np
import pandas as pd

from data_refinery_common.job_lookup import PipelineEnum
from data_refinery_common.logging import get_and_configure_logger
from data_refinery_common.models import (
    ComputationalResult,
    ComputedFile,
    OriginalFile,
    Pipeline,
    SampleResultAssociation,
)
from data_refinery_common.utils import get_env_variable, calculate_file_size, calculate_sha1
from data_refinery_workers.processors import utils, smashing_utils
from urllib.parse import quote


RESULTS_BUCKET = get_env_variable("S3_RESULTS_BUCKET_NAME", "refinebio-results-bucket")
S3_BUCKET_NAME = get_env_variable("S3_BUCKET_NAME", "data-refinery")
BODY_HTML = Path('data_refinery_workers/processors/smasher_email.min.html').read_text().replace('\n', '')
BODY_ERROR_HTML = Path('data_refinery_workers/processors/smasher_email_error.min.html').read_text().replace('\n', '')
BYTES_IN_GB = 1024 * 1024 * 1024
logger = get_and_configure_logger(__name__)
### DEBUG ###
logger.setLevel(logging.getLevelName('DEBUG'))


SCALERS = {
    'MINMAX': preprocessing.MinMaxScaler,
    'STANDARD': preprocessing.StandardScaler,
    'ROBUST': preprocessing.RobustScaler,
}


def log_state(message, job, start_time=False):
    if logger.isEnabledFor(logging.DEBUG):
        process = psutil.Process(os.getpid())
        ram_in_GB = process.memory_info().rss / BYTES_IN_GB
        logger.debug(message,
                     total_cpu=psutil.cpu_percent(),
                     process_ram=ram_in_GB,
                     job_id=job.id)

        if start_time:
            logger.debug('Duration: %s' % (time.time() - start_time), job_id=job.id)
        else:
            return time.time()


def _prepare_files(job_context: Dict) -> Dict:
    """
    Fetches and prepares the files to smash.
    """
    start_prepare_files = log_state("start prepare files", job_context["job"])
    found_files = False
    job_context['input_files'] = {}
    # `key` can either be the species name or experiment accession.
    for key, samples in job_context["samples"].items():
        smashable_files = []
        seen_files = set()
        for sample in samples:
            smashable_file = sample.get_most_recent_smashable_result_file()
            if smashable_file is not None and smashable_file not in seen_files:
                smashable_files = smashable_files + [(smashable_file, sample)]
                seen_files.add(smashable_file)
                found_files = True

        job_context['input_files'][key] = smashable_files

    if not found_files:
        error_message = "Couldn't get any files to smash for Smash job!!"
        logger.error(error_message,
                     dataset_id=job_context['dataset'].id,
                     num_samples=len(job_context["samples"]))

        # Delay failing this pipeline until the failure notify has been sent
        job_context['dataset'].failure_reason = error_message
        job_context['dataset'].success = False
        job_context['dataset'].save()
        job_context['job'].success = False
        job_context["job"].failure_reason = "Couldn't get any files to smash for Smash job - empty all_sample_files"
        return job_context

    job_context["work_dir"] = "/home/user/data_store/smashed/" + str(job_context["dataset"].pk) + "/"
    # Ensure we have a fresh smash directory
    shutil.rmtree(job_context["work_dir"], ignore_errors=True)
    os.makedirs(job_context["work_dir"])

    job_context["output_dir"] = job_context["work_dir"] + "output/"
    os.makedirs(job_context["output_dir"])
    log_state("end prepare files", job_context["job"], start_prepare_files)
    return job_context


def _add_annotation_column(annotation_columns, column_name):
    """Add annotation column names in place.
    Any column_name that starts with "refinebio_" will be skipped.
    """

    if not column_name.startswith("refinebio_"):
        annotation_columns.add(column_name)


def _get_tsv_columns(job_context, samples_metadata):
    """Returns an array of strings that will be written as a TSV file's
    header. The columns are based on fields found in samples_metadata.

    Some nested annotation fields are taken out as separate columns
    because they are more important than the others.
    """
    tsv_start = log_state("start get tsv columns", job_context["job"])
    refinebio_columns = set()
    annotation_columns = set()
    for sample_metadata in samples_metadata.values():
        for meta_key, meta_value in sample_metadata.items():
            if meta_key != 'refinebio_annotations':
                refinebio_columns.add(meta_key)
                continue

            # Decompose sample_metadata["annotations"], which is an array of annotations!
            for annotation in meta_value:
                for annotation_key, annotation_value in annotation.items():
                    # For ArrayExpress samples, take out the fields
                    # nested in "characteristic" as separate columns.
                    if (sample_metadata.get('refinebio_source_database', '') == "ARRAY_EXPRESS"
                        and annotation_key == "characteristic"):
                        for pair_dict in annotation_value:
                            if 'category' in pair_dict and 'value' in pair_dict:
                                _add_annotation_column(annotation_columns, pair_dict['category'])
                    # For ArrayExpress samples, also take out the fields
                    # nested in "variable" as separate columns.
                    elif (sample_metadata.get('refinebio_source_database', '') == "ARRAY_EXPRESS"
                          and annotation_key == "variable"):
                        for pair_dict in annotation_value:
                            if 'name' in pair_dict and 'value' in pair_dict:
                                _add_annotation_column(annotation_columns, pair_dict['name'])
                    # For ArrayExpress samples, skip "source" field
                    elif (sample_metadata.get('refinebio_source_database', '') == "ARRAY_EXPRESS"
                          and annotation_key == "source"):
                        continue
                    # For GEO samples, take out the fields nested in
                    # "characteristics_ch1" as separate columns.
                    elif (sample_metadata.get('refinebio_source_database', '') == "GEO"
                          and annotation_key == "characteristics_ch1"): # array of strings
                        for pair_str in annotation_value:
                            if ':' in pair_str:
                                tokens = pair_str.split(':', 1)
                                _add_annotation_column(annotation_columns, tokens[0])
                    # Saves all other annotation fields in separate columns
                    else:
                        _add_annotation_column(annotation_columns, annotation_key)

    # Return sorted columns, in which "refinebio_accession_code" and "experiment_accession" are
    # always first, followed by the other refinebio columns (in alphabetic order), and
    # annotation columns (in alphabetic order) at the end.
    refinebio_columns.discard('refinebio_accession_code')
    log_state("end get tsv columns", job_context["job"], tsv_start)
    return ['refinebio_accession_code', 'experiment_accession'] + sorted(refinebio_columns) \
        + sorted(annotation_columns)


def _add_annotation_value(row_data, col_name, col_value, sample_accession_code):
    """Adds a new `col_name` key whose value is `col_value` to row_data.
    If col_name already exists in row_data with different value, print
    out a warning message.
    """
    # Generate a warning message if annotation field name starts with
    # "refinebio_".  This should rarely (if ever) happen.
    if col_name.startswith("refinebio_"):
        logger.warning(
            "Annotation value skipped",
            annotation_field=col_name,
            annotation_value=col_value,
            sample_accession_code=sample_accession_code
        )
    elif col_name not in row_data:
        row_data[col_name] = col_value
    # Generate a warning message in case of conflicts of annotation values.
    # (Requested by Dr. Jackie Taroni)
    elif row_data[col_name] != col_value:
        logger.warning(
            "Conflict of values found in column %s: %s vs. %s" % (
                col_name, row_data[col_name], col_value),
            sample_accession_code=sample_accession_code
        )


def _get_experiment_accession(sample_accession_code, dataset_data):
    for experiment_accession, samples in dataset_data.items():
        if sample_accession_code in samples:
            return experiment_accession
    return ""  # Should never happen, because the sample is by definition in the dataset


def _get_tsv_row_data(sample_metadata, dataset_data):
    """Returns field values based on input sample_metadata.

    Some annotation fields are treated specially because they are more
    important.  See `_get_tsv_columns` function above for details.
    """

    sample_accession_code = sample_metadata.get('refinebio_accession_code', '')
    row_data = dict()
    for meta_key, meta_value in sample_metadata.items():
        # If the field is a refinebio-specific field, simply copy it.
        if meta_key != 'refinebio_annotations':
            row_data[meta_key] = meta_value
            continue

        # Decompose sample_metadata["refinebio_annotations"], which is
        # an array of annotations.
        for annotation in meta_value:
            for annotation_key, annotation_value in annotation.items():
                # "characteristic" in ArrayExpress annotation
                if (sample_metadata.get('refinebio_source_database', '') == "ARRAY_EXPRESS"
                    and annotation_key == "characteristic"):
                    for pair_dict in annotation_value:
                        if 'category' in pair_dict and 'value' in pair_dict:
                            col_name, col_value = pair_dict['category'], pair_dict['value']
                            _add_annotation_value(row_data, col_name, col_value,
                                                  sample_accession_code)
                # "variable" in ArrayExpress annotation
                elif (sample_metadata.get('refinebio_source_database', '') == "ARRAY_EXPRESS"
                      and annotation_key == "variable"):
                    for pair_dict in annotation_value:
                        if 'name' in pair_dict and 'value' in pair_dict:
                            col_name, col_value = pair_dict['name'], pair_dict['value']
                            _add_annotation_value(row_data, col_name, col_value,
                                                  sample_accession_code)
                 # Skip "source" field ArrayExpress sample's annotation
                elif (sample_metadata.get('refinebio_source_database', '') == "ARRAY_EXPRESS"
                      and annotation_key == "source"):
                    continue
                # "characteristics_ch1" in GEO annotation
                elif (sample_metadata.get('refinebio_source_database', '') == "GEO"
                      and annotation_key == "characteristics_ch1"): # array of strings
                    for pair_str in annotation_value:
                        if ':' in pair_str:
                            col_name, col_value = pair_str.split(':', 1)
                            col_value = col_value.strip()
                            _add_annotation_value(row_data, col_name, col_value,
                                                  sample_accession_code)
                # If annotation_value includes only a 'name' key, extract its value directly:
                elif (isinstance(annotation_value, dict)
                      and len(annotation_value) == 1 and 'name' in annotation_value):
                    _add_annotation_value(row_data, annotation_key, annotation_value['name'],
                                          sample_accession_code)
                # If annotation_value is a single-element array, extract the element directly:
                elif isinstance(annotation_value, list) and len(annotation_value) == 1:
                    _add_annotation_value(row_data, annotation_key, annotation_value[0],
                                          sample_accession_code)
                # Otherwise save all annotation fields in separate columns
                else:
                    _add_annotation_value(row_data, annotation_key, annotation_value,
                                          sample_accession_code)

    row_data["experiment_accession"] = _get_experiment_accession(sample_accession_code,
                                                                 dataset_data)

    return row_data


def _write_tsv_json(job_context, metadata):
    """Writes tsv files on disk.
    If the dataset is aggregated by species, also write species-level
    JSON file.
    """

    # Uniform TSV header per dataset
    columns = _get_tsv_columns(job_context, metadata['samples'])

    # Per-Experiment Metadata
    if job_context["dataset"].aggregate_by == "EXPERIMENT":
        tsv_paths = []
        for experiment_title, experiment_data in metadata['experiments'].items():
            experiment_dir = job_context["output_dir"] + experiment_title + '/'
            experiment_dir = experiment_dir.encode('ascii', 'ignore')
            os.makedirs(experiment_dir, exist_ok=True)
            tsv_path = experiment_dir.decode("utf-8") + 'metadata_' + experiment_title + '.tsv'
            tsv_path = tsv_path.encode('ascii', 'ignore')
            tsv_paths.append(tsv_path)
            with open(tsv_path, 'w', encoding='utf-8') as tsv_file:
                dw = csv.DictWriter(tsv_file, columns, delimiter='\t')
                dw.writeheader()
                for sample_accession_code, sample_metadata in metadata['samples'].items():
                    if sample_accession_code in experiment_data['sample_accession_codes']:
                        row_data = _get_tsv_row_data(sample_metadata, job_context["dataset"].data)
                        dw.writerow(row_data)
        return tsv_paths
    # Per-Species Metadata
    elif job_context["dataset"].aggregate_by == "SPECIES":
        tsv_paths = []
        for species in job_context['input_files'].keys():
            species_dir = job_context["output_dir"] + species + '/'
            os.makedirs(species_dir, exist_ok=True)
            samples_in_species = []
            tsv_path = species_dir + "metadata_" + species + '.tsv'
            tsv_paths.append(tsv_path)
            with open(tsv_path, 'w', encoding='utf-8') as tsv_file:
                dw = csv.DictWriter(tsv_file, columns, delimiter='\t')
                dw.writeheader()
                for sample_metadata in metadata['samples'].values():
                    if sample_metadata.get('refinebio_organism', '') == species:
                        row_data = _get_tsv_row_data(sample_metadata, job_context["dataset"].data)
                        dw.writerow(row_data)
                        samples_in_species.append(sample_metadata)

            # Writes a json file for current species:
            if len(samples_in_species):
                species_metadata = {
                    'species': species,
                    'samples': samples_in_species
                }
                json_path = species_dir + "metadata_" + species + '.json'
                with open(json_path, 'w', encoding='utf-8') as json_file:
                    json.dump(species_metadata, json_file, indent=4, sort_keys=True)
        return tsv_paths
    # All Metadata
    else:
        all_dir = job_context["output_dir"] + "ALL/"
        os.makedirs(all_dir, exist_ok=True)
        tsv_path = all_dir + 'metadata_ALL.tsv'
        with open(tsv_path, 'w', encoding='utf-8') as tsv_file:
            dw = csv.DictWriter(tsv_file, columns, delimiter='\t')
            dw.writeheader()
            for sample_metadata in metadata['samples'].values():
                row_data = _get_tsv_row_data(sample_metadata, job_context["dataset"].data)
                dw.writerow(row_data)
        return [tsv_path]


def _quantile_normalize(job_context: Dict, ks_check=True, ks_stat=0.001) -> Dict:
    """
    Apply quantile normalization.

    """
    # Prepare our QN target file
    organism = job_context['organism']
    qn_target = utils.get_most_recent_qn_target_for_organism(organism)

    if not qn_target:
        logger.error("Could not find QN target for Organism!",
            organism=organism,
            dataset_id=job_context['dataset'].id,
            processor_job_id=job_context["job"].id,
        )
        job_context['dataset'].success = False
        job_context['job'].failure_reason = "Could not find QN target for Organism: " + str(organism)
        job_context['dataset'].failure_reason = "Could not find QN target for Organism: " + str(organism)
        job_context['dataset'].save()
        job_context['job'].success = False
        job_context['failure_reason'] = "Could not find QN target for Organism: " + str(organism)
        return job_context
    else:
        qn_target_path = qn_target.sync_from_s3()
        qn_target_frame = pd.read_csv(qn_target_path, sep='\t', header=None,
                                      index_col=None, error_bad_lines=False)

        # Prepare our RPy2 bridge
        pandas2ri.activate()
        preprocessCore = importr('preprocessCore')
        as_numeric = rlang("as.numeric")
        data_matrix = rlang('data.matrix')

        # Convert the smashed frames to an R numeric Matrix
        # and the target Dataframe into an R numeric Vector
        target_vector = as_numeric(qn_target_frame[0])
        merged_matrix = data_matrix(job_context['merged_no_qn'])

        # Perform the Actual QN
        reso = preprocessCore.normalize_quantiles_use_target(
                                            x=merged_matrix,
                                            target=target_vector,
                                            copy=True
                                        )

        # Verify this QN, related: https://github.com/AlexsLemonade/refinebio/issues/599#issuecomment-422132009
        set_seed = rlang("set.seed")
        combn = rlang("combn")
        ncol = rlang("ncol")
        ks_test = rlang("ks.test")
        which = rlang("which")

        set_seed(123)

        n = ncol(reso)[0]
        m = 2
        if n >= m:
            combos = combn(ncol(reso), 2)

            # Convert to NP, Shuffle, Return to R
            ar = np.array(combos)
            np.random.shuffle(np.transpose(ar))
            nr, nc = ar.shape
            combos = ro.r.matrix(ar, nrow=nr, ncol=nc)

            # adapted from
            # https://stackoverflow.com/questions/9661469/r-t-test-over-all-columns
            # apply KS test to randomly selected pairs of columns (samples)
            for i in range(1, min(ncol(combos)[0], 100)):
                value1 = combos.rx(1, i)[0]
                value2 = combos.rx(2, i)[0]

                test_a = reso.rx(True, value1)
                test_b = reso.rx(True, value2)

                # RNA-seq has a lot of zeroes in it, which
                # breaks the ks_test. Therefore we want to
                # filter them out. To do this we drop the
                # lowest half of the values. If there's
                # still zeroes in there, then that's
                # probably too many zeroes so it's okay to
                # fail.
                median_a = np.median(test_a)
                median_b = np.median(test_b)

                # `which` returns indices which are
                # 1-indexed. Python accesses lists with
                # zero-indexes, even if that list is
                # actually an R vector. Therefore subtract
                # 1 to account for the difference.
                test_a = [test_a[i-1] for i in which(test_a > median_a)]
                test_b = [test_b[i-1] for i in which(test_b > median_b)]

                # The python list comprehension gives us a
                # python list, but ks_test wants an R
                # vector so let's go back.
                test_a = as_numeric(test_a)
                test_b = as_numeric(test_b)

                ks_res = ks_test(test_a, test_b)
                statistic = ks_res.rx('statistic')[0][0]
                pvalue = ks_res.rx('p.value')[0][0]

                job_context['ks_statistic'] = statistic
                job_context['ks_pvalue'] = pvalue

                # We're unsure of how strigent to be about
                # the pvalue just yet, so we're extra lax
                # rather than failing tons of tests. This may need tuning.
                if ks_check:
                    if statistic > ks_stat or pvalue < 0.8:
                        job_context['ks_warning'] = ("Failed Kolmogorov Smirnov test! Stat: " +
                                        str(statistic) + ", PVal: " + str(pvalue))
        else:
            logger.warning("Not enough columns to perform KS test - either bad smash or single saple smash.",
                dataset_id=job_context['dataset'].id)

        # And finally convert back to Pandas
        ar = np.array(reso)
        new_merged = pd.DataFrame(ar, columns=job_context['merged_no_qn'].columns, index=job_context['merged_no_qn'].index)
        job_context['merged_qn'] = new_merged
        merged = new_merged
    return job_context

def sync_quant_files(output_path, files_sample_tuple, job_context: Dict):
    """ Takes a list of ComputedFiles and copies the ones that are quant files to the provided directory.
        Returns the total number of samples that were included """
    num_samples = 0
    for (_, sample) in files_sample_tuple:
        latest_computed_file = sample.get_most_recent_quant_sf_file()
        # we just want to output the quant.sf files
        if not latest_computed_file: continue
        accession_code = sample.accession_code
        # copy file to the output path
        output_file_path = output_path + accession_code + "_quant.sf"
        num_samples += 1
        latest_computed_file.get_synced_file_path(path=output_file_path)
    return num_samples

def _inner_join(job_context: Dict) -> pd.DataFrame:
    """Performs an inner join across the all_frames key of job_context.

    Returns a new dict containing the metadata, not the job_context.
    """
    # Merge all of the frames we've gathered into a single big frame, skipping duplicates.
    # TODO: If the very first frame is the wrong platform, are we boned?
    # TODO: I think I'd like to not have all_frames be directly on the job context and overwritten.
    # Hmm, now I'm less sure because I think that means we wouldn't overwrite them. Perhaps if I do in the
    merged = job_context['all_frames'][0]
    i = 1

    old_len_merged = len(merged)
    merged_backup = merged

    while i < len(job_context['all_frames']):
        frame = job_context['all_frames'][i]
        i = i + 1

        if i % 1000 == 0:
            logger.info("Smashing keyframe",
                        i=i,
                        job_id=job_context['job'].id)

        # I'm not sure where these are sneaking in from, but we don't want them.
        # Related: https://github.com/AlexsLemonade/refinebio/issues/390
        breaker = False
        for column in frame.columns:
            if column in merged.columns:
                breaker = True

        if breaker:
            logger.warning("Column repeated for smash job!",
                           input_files=str(input_files),
                           dataset_id=job_context["dataset"].id,
                           job_id=job_context["job"].id,
                           column=column)
            continue

        # This is the inner join, the main "Smash" operation
        merged = merged.merge(frame, how='inner', left_index=True, right_index=True)

        new_len_merged = len(merged)
        if new_len_merged < old_len_merged:
            logger.warning("Dropped rows while smashing!",
                dataset_id=job_context["dataset"].id,
                old_len_merged=old_len_merged,
                new_len_merged=new_len_merged
            )
        if new_len_merged == 0:
            logger.warning("Skipping a bad merge frame!",
                           dataset_id=job_context["dataset"].id,
                           job_id=job_context["job"].id,
                           old_len_merged=old_len_merged,
                           new_len_merged=new_len_merged,
                           bad_frame_number=i,)
            merged = merged_backup
            new_len_merged = len(merged)
            try:
                job_context['unsmashable_files'].append(frame.columns[0])
            except Exception:
                # Something is really, really wrong with this frame.
                pass

        old_len_merged = len(merged)
        merged_backup = merged

    return merged


# 'how' can almost certainly be removed because I think it's just used
# to switch the behavior between smasher/compendi
# But I think maybe this is actually the function I wanna call from
# compendia so the switching behaviour is needed?
# Nooooo, I think I wanna call process frame from compendia, not this.
# Therefore this can just always do an inner join and then the
# compendia can do its own joining.
def smash_key(job_context: Dict,
              key: str,
              input_files: List[ComputedFile],
              how="inner") -> Dict:
    """Smash all of the input files together for a given key.

    Steps:
        Combine common genes (pandas merge)
        Transpose such that genes are columns (features)
        Scale features with sci-kit learn
        Transpose again such that samples are columns and genes are rows
    """
    # Check if we need to copy the quant.sf files
    if job_context['dataset'].quant_sf_only:
        outfile_dir = job_context["output_dir"] + key + "/"
        os.makedirs(outfile_dir, exist_ok=True)
        job_context['num_samples'] += sync_quant_files(outfile_dir, input_files, job_context)
        # we ONLY want to give quant sf files to the user if that's what they requested
        return job_context

    job_context = smashing_utils.process_frames_for_key(key, input_files, job_context)
    if len(job_context['all_frames']) < 1:
        logger.error("Was told to smash a key with no frames!",
                       job_id=job_context['job'].id,
                       key=key)
        # TODO: is this the proper way to handle this? I can see us
        # not wanting to fail an entire dataset because one experiment
        # had a problem, but I also think it could be problematic to
        # just skip an experiment and pretend nothing went wrong.
        return job_context

    # Combine the two technologies into a single list of dataframes.
    ## Extend one list rather than adding the two together so we don't
    ## the memory both are using.
    job_context['rnaseq_frames'].extend(job_context['microarray_frames'])
    ## Free up the the memory the microarray-only list was using.
    job_context.pop('microarray_frames')
    ## Change the key of the now-extended list
    job_context['all_frames'] = job_context.pop('rnaseq_frames')

    if how == "inner":
        merged = _inner_join(job_context)
    else:
        merged = pd.concat(job_context['all_frames'],
                           axis=1,
                           keys=None,
                           join='outer',
                           copy=False,
                           sort=True)

    job_context['original_merged'] = merged
    log_state("end build all frames", job_context["job"], start_smash)
    start_qn = log_state("start qn", job_context["job"], start_smash)

    # Quantile Normalization
    if job_context['dataset'].quantile_normalize:
        try:
            job_context['merged_no_qn'] = merged
            job_context['organism'] = job_context['dataset'].get_samples().first().organism
            job_context = _quantile_normalize(job_context)
            merged = job_context.get('merged_qn', None)

            # We probably don't have an QN target or there is another error,
            # so let's fail gracefully.
            assert merged is not None, "Problem occured during quantile normalization: No merged_qn"
        except Exception as e:
            logger.exception("Problem occured during quantile normalization",
                dataset_id=job_context['dataset'].id,
                processor_job_id=job_context["job"].id,
            )
            job_context['dataset'].success = False

            if not job_context['job'].failure_reason:
                job_context['job'].failure_reason = "Failure reason: " + str(e)
                job_context['dataset'].failure_reason = "Failure reason: " + str(e)

            job_context['dataset'].save()
            # Delay failing this pipeline until the failure notify has been sent
            job_context['job'].success = False
            job_context['failure_reason'] = str(e)
            return job_context

    # End QN
    log_state("end qn", job_context["job"], start_qn)
    # Transpose before scaling
    # Do this even if we don't want to scale in case transpose
    # modifies the data in any way. (Which it shouldn't but
    # we're paranoid.)
    # TODO: stop the paranoia because Josh has alleviated it.
    transposed = merged.transpose()
    start_scaler = log_state("starting scaler", job_context["job"])
    # Scaler
    if job_context['dataset'].scale_by != "NONE":
        scale_funtion = SCALERS[job_context['dataset'].scale_by]
        scaler = scale_funtion(copy=True)
        scaler.fit(transposed)
        scaled = pd.DataFrame(  scaler.transform(transposed),
                                index=transposed.index,
                                columns=transposed.columns
                            )
        # Untranspose
        untransposed = scaled.transpose()
    else:
        # Wheeeeeeeeeee
        untransposed = transposed.transpose()
    log_state("end scaler", job_context["job"], start_scaler)

    # This is just for quality assurance in tests.
    job_context['final_frame'] = untransposed

    # Write to temp file with dataset UUID in filename.
    subdir = ''
    if job_context['dataset'].aggregate_by in ["SPECIES", "EXPERIMENT"]:
        subdir = key
    elif job_context['dataset'].aggregate_by == "ALL":
        subdir = "ALL"

    # Normalize the Header format
    untransposed.index.rename('Gene', inplace=True)

    outfile_dir = job_context["output_dir"] + key + "/"
    os.makedirs(outfile_dir, exist_ok=True)
    outfile = outfile_dir + key + ".tsv"
    job_context['smash_outfile'] = outfile
    untransposed.to_csv(outfile, sep='\t', encoding='utf-8')

    return job_context


# 'how' can almost certainly be removed because I think it's just used
# to switch the behavior between smasher/compendia
def _smash_all(job_context: Dict, how="inner") -> Dict:
    """Perform smashing on all species/experiments in the dataset.
    """
    start_smash = log_state("start smash", job_context["job"])
    # We have already failed - return now so we can send our fail email.
    if job_context['dataset'].failure_reason not in ['', None]:
        return job_context

    try:
        job_context['unsmashable_files'] = []
        job_context['num_samples'] = 0

        # Smash all of the sample sets
        logger.debug("About to smash!",
                     dataset_count=len(job_context['dataset'].data),
                     job_id=job_context['job'].id)

        # Once again, `key` is either a species name or an experiment accession
        for key, input_files in job_context['input_files'].items():
            job_context = _smash_key(job_context, key, input_files, how)

        # Copy LICENSE.txt and README.md files
        shutil.copy("README_DATASET.md", job_context["output_dir"] + "README.md")
        shutil.copy("LICENSE_DATASET.txt", job_context["output_dir"] + "LICENSE.TXT")

        metadata = smashing_utils.compile_metadata(job_context)

        # Write samples metadata to TSV
        try:
            tsv_paths = _write_tsv_json(job_context, metadata, job_context["output_dir"])
            job_context['metadata_tsv_paths'] = tsv_paths
            # Metadata to JSON
            metadata['created_at'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
            json_metadata_path = job_context["output_dir"] + 'aggregated_metadata.json'
            with open(json_metadata_path, 'w', encoding='utf-8') as metadata_file:
                json.dump(metadata, metadata_file, indent=4, sort_keys=True)
        except Exception as e:
            logger.exception("Failed to write metadata TSV!",
                job_id = job_context['job'].id)
            job_context['metadata_tsv_paths'] = None
        metadata['files'] = os.listdir(job_context["output_dir"])

        # Finally, compress all files into a zip
        final_zip_base = "/home/user/data_store/smashed/" + str(job_context["dataset"].pk)
        shutil.make_archive(final_zip_base, 'zip', job_context["output_dir"])
        job_context["output_file"] = final_zip_base + ".zip"
    except Exception as e:
        logger.exception("Could not smash dataset.",
                        dataset_id=job_context['dataset'].id,
                        processor_job_id=job_context['job_id'],
                        num_input_files=len(job_context['input_files']))
        job_context['dataset'].success = False
        job_context['job'].failure_reason = "Failure reason: " + str(e)
        job_context['dataset'].failure_reason = "Failure reason: " + str(e)
        job_context['dataset'].save()
        # Delay failing this pipeline until the failure notify has been sent
        job_context['job'].success = False
        job_context['failure_reason'] = str(e)
        return job_context
    job_context['metadata'] = metadata
    job_context['dataset'].success = True
    job_context['dataset'].save()

    logger.debug("Created smash output!",
        archive_location=job_context["output_file"])

    log_state("end smash", job_context["job"], start_smash);
    return job_context


def _upload(job_context: Dict) -> Dict:
    """ Uploads the result file to S3 and notifies user. """

    # There has been a failure already, don't try to upload anything.
    if not job_context.get("output_file", None):
        logger.error("Was told to upload a smash result without an output_file.")
        return job_context

    try:
        if job_context.get("upload", True) and settings.RUNNING_IN_CLOUD:
            s3_client = boto3.client('s3')

            # Note that file expiry is handled by the S3 object lifecycle,
            # managed by terraform.
            s3_client.upload_file(
                    job_context["output_file"],
                    RESULTS_BUCKET,
                    job_context["output_file"].split('/')[-1],
                    ExtraArgs={'ACL':'public-read'}
                )
            result_url = ("https://s3.amazonaws.com/" + RESULTS_BUCKET + "/" +
                          job_context["output_file"].split('/')[-1])

            job_context["result_url"] = result_url

            logger.debug("Result uploaded!",
                    result_url=job_context["result_url"]
                )

            job_context["dataset"].s3_bucket = RESULTS_BUCKET
            job_context["dataset"].s3_key = job_context["output_file"].split('/')[-1]
            job_context["dataset"].size_in_bytes = calculate_file_size(job_context["output_file"])
            job_context["dataset"].sha1 = calculate_sha1(job_context["output_file"])

            job_context["dataset"].save()

            # File is uploaded, we can delete the local.
            try:
                os.remove(job_context["output_file"])
            except OSError:
                pass

    except Exception as e:
        logger.exception("Failed to upload smash result file.", file=job_context["output_file"])
        job_context['job'].success = False
        job_context['job'].failure_reason = "Failure reason: " + str(e)
        # Delay failing this pipeline until the failure notify has been sent
        # job_context['success'] = False

    return job_context

def _notify(job_context: Dict) -> Dict:
    """ Use AWS SES to notify a user of a smash result.. """

    ##
    # SES
    ##
    if job_context.get("upload", True) and settings.RUNNING_IN_CLOUD:
        # Link to the dataset page, where the user can re-try the download job
        dataset_url = 'https://www.refine.bio/dataset/' + str(job_context['dataset'].id)

        # Send a notification to slack when a dataset fails to be processed
        if job_context['job'].failure_reason not in ['', None]:
            try:
                requests.post(
                    "https://hooks.slack.com/services/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",
                    json={
                        'fallback': 'Dataset failed processing.',
                        'title': 'Dataset failed processing',
                        'title_link': dataset_url,
                        "attachments":[
                            {
                                "color": "warning",
                                "text": job_context['job'].failure_reason,
                                'author_name': job_context["dataset"].email_address,
                                'fields': [
                                    {
                                        'title': 'Dataset id',
                                        'value': str(job_context['dataset'].id)
                                    }
                                ]
                            }
                        ]
                    },
                    headers={'Content-Type': 'application/json'},
                    timeout=10
                )
            except Exception as e:
                logger.warn(e) # It doens't really matter if this didn't work
                pass

        # Don't send an email if we don't have address.
        if job_context["dataset"].email_address:
            SENDER = "Refine.bio Mail Robot <noreply@refine.bio>"
            RECIPIENT = job_context["dataset"].email_address
            AWS_REGION = "us-east-1"
            CHARSET = "UTF-8"


            if job_context['job'].failure_reason not in ['', None]:
                SUBJECT = "There was a problem processing your refine.bio dataset :("
                BODY_TEXT = "We tried but were unable to process your requested dataset. Error was: \n\n" + str(job_context['job'].failure_reason) + "\nDataset ID: " + str(job_context['dataset'].id) + "\n We have been notified and are looking into the problem. \n\nSorry!"

                ERROR_EMAIL_TITLE = quote('I can\'t download my dataset')
                ERROR_EMAIL_BODY = quote("""
                [What browser are you using?]
                [Add details of the issue you are facing]

                ---
                """ + str(job_context['dataset'].id))

                FORMATTED_HTML = BODY_ERROR_HTML.replace('REPLACE_DATASET_URL', dataset_url)\
                                                .replace('REPLACE_ERROR_TEXT', job_context['job'].failure_reason)\
                                                .replace('REPLACE_NEW_ISSUE', 'https://github.com/AlexsLemonade/refinebio/issues/new?title={0}&body={1}&labels=bug'.format(ERROR_EMAIL_TITLE, ERROR_EMAIL_BODY))\
                                                .replace('REPLACE_MAILTO', 'mailto:ccdl@alexslemonade.org?subject={0}&body={1}'.format(ERROR_EMAIL_TITLE, ERROR_EMAIL_BODY))
                job_context['success'] = False
            else:
                SUBJECT = "Your refine.bio Dataset is Ready!"
                BODY_TEXT = "Hot off the presses:\n\n" + dataset_url + "\n\nLove!,\nThe refine.bio Team"
                FORMATTED_HTML = BODY_HTML.replace('REPLACE_DOWNLOAD_URL', dataset_url)\
                                          .replace('REPLACE_DATASET_URL', dataset_url)

            # Try to send the email.
            try:

                # Create a new SES resource and specify a region.
                client = boto3.client('ses', region_name=AWS_REGION)

                #Provide the contents of the email.
                response = client.send_email(
                    Destination={
                        'ToAddresses': [
                            RECIPIENT,
                        ],
                    },
                    Message={
                        'Body': {
                            'Html': {
                                'Charset': CHARSET,
                                'Data': FORMATTED_HTML,
                            },
                            'Text': {
                                'Charset': CHARSET,
                                'Data': BODY_TEXT,
                            },
                        },
                        'Subject': {
                            'Charset': CHARSET,
                            'Data': SUBJECT,
                        }
                    },
                    Source=SENDER,
                )
            # Display an error if something goes wrong.
            except ClientError as e:
                logger.warn("ClientError while notifying.", exc_info=1, client_error_message=e.response['Error']['Message'])
                job_context['job'].success = False
                job_context['job'].failure_reason = e.response['Error']['Message']
                job_context['success'] = False
                return job_context
            except Exception as e:
                logger.warn("General failure when trying to send email.", exc_info=1, result_url=job_context["result_url"])
                job_context['job'].success = False
                job_context['job'].failure_reason = str(e)
                job_context['success'] = False
                return job_context

            job_context["dataset"].email_sent = True
            job_context["dataset"].save()

    # Handle non-cloud too
    if job_context['job'].failure_reason:
        job_context['success'] = False

    return job_context

def _update_result_objects(job_context: Dict) -> Dict:
    """Closes out the dataset object."""

    dataset = job_context["dataset"]
    dataset.is_processing = False
    dataset.is_processed = True
    dataset.is_available = True
    dataset.expires_on = timezone.now() + timedelta(days=7)
    dataset.save()

    job_context['success'] = True

    return job_context

def smash(job_id: int, upload=True) -> None:
    """ Main Smasher interface """

    pipeline = Pipeline(name=PipelineEnum.SMASHER.value)
    return utils.run_pipeline({ "job_id": job_id,
                                "upload": upload,
                                "pipeline": pipeline
                            },
                       [utils.start_job,
                        smashing_utils.prepare_files,
                        _smash,
                        _upload,
                        _notify,
                        _update_result_objects,
                        utils.end_job])
