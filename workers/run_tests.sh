#!/bin/sh
# shellcheck disable=2103

# Script for executing Django PyUnit tests within a Docker container.

# Exit on failure
set -e

print_description() {
    echo "Runs the tests for workers. These tests require different Docker containers"
    echo "depending on which code will be tested. By default all tests in the workers"
    echo "project are run."
}

print_options() {
    echo "Options:"
    echo "    -h       Prints the help message"
    echo "    -t TAG   Runs all tests that are tagged with \$TAG"
}

while getopts ":t:h" opt; do
    case $opt in
        t)
            tag=$OPTARG
            ;;
        h)
            print_description
            echo
            print_options
            exit 0
            ;;
        \?)
            echo "Invalid option: -$OPTARG" >&2
            print_options >&2
            exit 1
            ;;
        :)
            echo "Option -$OPTARG requires an argument." >&2
            print_options >&2
            exit 1
            ;;
    esac
done

# This script should always run as if it were being called from
# the directory it lives in.
script_directory="$(perl -e 'use File::Basename;
 use Cwd "abs_path";
 print dirname(abs_path(@ARGV[0]));' -- "$0")"
cd "$script_directory" || exit

# However in order to give Docker access to all the code we have to
# move up a level
cd ..

# Ensure that postgres is running
if ! [ "$(docker ps --filter name=drdb -q)" ]; then
    echo "You must start Postgres first with:" >&2
    echo "./scripts/run_postgres.sh" >&2
    exit 1
fi

volume_directory="$script_directory/test_volume"

test_data_repo="https://s3.amazonaws.com/data-refinery-test-assets"

if [ -z "$tag" ] || [ "$tag" = "salmon" ]; then
    # Download "salmon quant" test data The `newer` file was to
    # signify that we using updated data. However the data has been
    # updated again so now we need to go back to checking to make sure
    # that it's not there so we know we have even NEWER data.
    if [ ! -e "$volume_directory/salmon_tests" ] || [ -e "$volume_directory/salmon_tests/newer" ]; then
        # Remove the data that comes from S3 so anything old is blown away.
        rm -rf "$volume_directory/salmon_tests"

        echo "Downloading 'salmon quant' test data..."
        wget -q -O "$volume_directory"/salmon_tests.tar.gz "$test_data_repo"/salmon_tests_newer.tar.gz
        tar xzf "$volume_directory"/salmon_tests.tar.gz -C "$volume_directory"
        rm "$volume_directory"/salmon_tests.tar.gz
    fi

    # Download salmontools test data
    rm -rf "$volume_directory"/salmontools/
    git clone https://github.com/dongbohu/salmontools_tests.git "$volume_directory"/salmontools

    # Download tximport test data
    rm -rf "$volume_directory"/tximport_test/
    git clone https://github.com/dongbohu/tximport_test.git "$volume_directory"/tximport_test

    # Make sure data for Salmon test is downloaded from S3.
    rna_seq_test_raw_dir="$volume_directory/raw/TEST/SALMON"
    read_1_name="ERR1562482_1.fastq.gz"
    read_2_name="ERR1562482_2.fastq.gz"
    dotsra_name="ERR1562482.sra"
    rna_seq_test_data_1="$rna_seq_test_raw_dir/$read_1_name"
    rna_seq_test_data_2="$rna_seq_test_raw_dir/$read_2_name"
    dotsra="$rna_seq_test_raw_dir/$dotsra_name"
    if [ ! -e "$rna_seq_test_data_1" ]; then
        mkdir -p "$rna_seq_test_raw_dir"
        echo "Downloading $read_1_name for Salmon tests."
        wget -q -O "$rna_seq_test_data_1" \
             "$test_data_repo/$read_1_name"
        echo "Downloading $read_2_name for Salmon tests."
        wget -q -O "$rna_seq_test_data_2" \
             "$test_data_repo/$read_2_name"
    fi
    if [ ! -e "$dotsra" ]; then
        mkdir -p "$rna_seq_test_raw_dir"
        echo "Downloading $dotsra_name for Salmon tests."
        wget -q -O "$dotsra" \
             "$test_data_repo/$dotsra_name"
    fi
fi

if [ -z "$tag" ] || [ "$tag" = "affymetrix" ]; then
    # Make sure CEL for test is downloaded from S3
    cel_name="GSM1426071_CD_colon_active_1.CEL"
    cel_name2="GSM45588.CEL"
    cel_name3="GSM1364667_U_110208_7-02-10_S2.CEL"
    pcl_name="GSM1426071_CD_colon_active_1.PCL"
    pcl_name2="GSM45588.PCL"
    pcl_name3="GSM1364667_U_110208_7-02-10_S2.PCL"
    cel_test_raw_dir="$volume_directory/raw/TEST/CEL"
    cel_test_data_1="$cel_test_raw_dir/$cel_name"
    cel_test_data_2="$cel_test_raw_dir/$cel_name2"
    cel_test_data_3="$cel_test_raw_dir/$cel_name3"
    pcl_test_dir="$volume_directory/TEST/PCL"
    pcl_test_data_1="$pcl_test_dir/$pcl_name"
    pcl_test_data_2="$pcl_test_dir/$pcl_name2"
    pcl_test_data_3="$pcl_test_dir/$pcl_name3"
    if [ ! -e "$cel_test_data_1" ]; then
        mkdir -p "$cel_test_raw_dir"
        echo "Downloading CEL for tests."
        wget -q -O "$cel_test_data_1" \
             "$test_data_repo/$cel_name"
    fi
    if [ ! -e "$cel_test_data_2" ]; then
        echo "Downloading Non-Brainarray CEL for tests."
        wget -q -O "$cel_test_data_2" \
             "$test_data_repo/$cel_name2"
    fi
    if [ ! -e "$cel_test_data_3" ]; then
        echo "Downloading Huex Brain Array CEL for tests."
        wget -q -O "$cel_test_data_3" \
             "$test_data_repo/$cel_name3"
    fi
    if [ ! -e "$pcl_test_data_1" ]; then
        mkdir -p "$pcl_test_dir"
        echo "Downloading pre-computed PCL for tests."
        wget -q -O "$pcl_test_data_1" \
             "$test_data_repo/$pcl_name"
    fi
    if [ ! -e "$pcl_test_data_2" ]; then
        mkdir -p "$pcl_test_dir"
        echo "Downloading pre-computed Non-Brainarray PCL for tests."
        wget -q -O "$pcl_test_data_2" \
             "$test_data_repo/$pcl_name2"
    fi
    if [ ! -e "$pcl_test_data_3" ]; then
        mkdir -p "$pcl_test_dir"
        echo "Downloading pre-computed Huex Brain Array PCL for tests."
        wget -q -O "$pcl_test_data_3" \
             "$test_data_repo/$pcl_name3"
    fi

fi

if [ -z "$tag" ] || [ "$tag" = "transcriptome" ]; then
    # Make sure data for Transcriptome Index tests is downloaded.
    tx_index_test_raw_dir="$volume_directory/raw/TEST/TRANSCRIPTOME_INDEX/AEGILOPS_TAUSCHII/"
    fasta_file="aegilops_tauschii_short.fa.gz"
    if [ ! -e "$tx_index_test_raw_dir/$fasta_file" ]; then
        mkdir -p "$tx_index_test_raw_dir"
        echo "Downloading fasta file for Transcriptome Index tests."
        wget -q -O "$tx_index_test_raw_dir/$fasta_file" \
             "$test_data_repo/$fasta_file"
    fi
    gtf_file="aegilops_tauschii_short.gtf.gz"
    if [ ! -e "$tx_index_test_raw_dir/$gtf_file" ]; then
        mkdir -p "$tx_index_test_raw_dir"
        echo "Downloading gtf file for Transcriptome Index tests."
        wget -q -O "$tx_index_test_raw_dir/$gtf_file" \
             "$test_data_repo/$gtf_file"
    fi
    tx_index_test_raw_dir2="$volume_directory/raw/TEST/TRANSCRIPTOME_INDEX/"
    gtf_file2="Homo_sapiens_testdata.gtf"
    if [ ! -e "$tx_index_test_raw_dir2/$gtf_file2" ]; then
        mkdir -p "$tx_index_test_raw_dir2"
        echo "Downloading second gtf file for Transcriptome Index tests."
        wget -q -O "$tx_index_test_raw_dir2/$gtf_file2" \
             "$test_data_repo/$gtf_file2"
    fi
fi

if [ -z "$tag" ] || [ "$tag" = "illumina" ]; then
    ilu_test_raw_dir="$volume_directory/raw/TEST/ILLUMINA"
    ilu_files='GSE22427_non-normalized.txt GSE54661_non_normalized.txt GSE106321_non-normalized.txt
GSE33814_trimmed_non-normalized.txt GSE112517_non-normalized.txt GSE48023_trimmed_non-normalized.txt
GSE41355_non-normalized.txt'
    mkdir -p "$ilu_test_raw_dir"

    i=1
    for ilu_file in $ilu_files; do
        if [ ! -e "$ilu_test_raw_dir/$ilu_file" ]; then
            echo "Downloading Illumina file $i for Illumina tests."
            wget -q -O "$ilu_test_raw_dir/$ilu_file" \
                "$test_data_repo/$ilu_file"
        fi

        i=$(( i + 1 ))
    done
    unset i


    ilu_test_ref_dir="$volume_directory/raw/TEST/ILLUMINA/reference"
    ilu_ref_file="Ad-Cre-2.AVG_Signal.tsv"
    if [ ! -e "$ilu_test_ref_dir/$ilu_ref_file" ]; then
        mkdir -p "$ilu_test_ref_dir"
        echo "Downloading Illumin reference file for Illumina tests."
        wget -q -O "$ilu_test_ref_dir/$ilu_ref_file" \
             "$test_data_repo/$ilu_ref_file"
    fi
fi

if [ -z "$tag" ] || [ "$tag" = "agilent" ]; then
    # Agilnt Two Color test file
    at_file="GSM466597_95899_agilent.txt"
    at_test_raw_dir="$volume_directory/raw/TEST/AGILENT_TWOCOLOR"
    if [ ! -e "$at_test_raw_dir/$at_file" ]; then
        mkdir -p "$at_test_raw_dir"
        echo "Downloading Agilent file for A2C tests."
        wget -q -O "$at_test_raw_dir/$at_file" \
             "$test_data_repo/$at_file"
    fi
fi
if [ -z "$tag" ] || [ "$tag" = "no_op" ]; then
    no_test_raw_dir="$volume_directory/raw/TEST/NO_OP"
    no_file1="GSM557500-tbl-1.txt"
    if [ ! -e "$no_test_raw_dir/$no_file1" ]; then
        mkdir -p "$no_test_raw_dir"
        echo "Downloading NOOP file1."
        wget -q -O "$no_test_raw_dir/$no_file1" \
             "$test_data_repo/$no_file1"
    fi
    no_file2="GSM1234847_sample_table.txt"
    if [ ! -e "$no_test_raw_dir/$no_file2" ]; then
        mkdir -p "$no_test_raw_dir"
        echo "Downloading NOOP file2."
        wget -q -O "$no_test_raw_dir/$no_file2" \
             "$test_data_repo/$no_file2"
    fi
    no_file3="GSM1234847_sample_table_headerless.txt"
    if [ ! -e "$no_test_raw_dir/$no_file3" ]; then
        mkdir -p "$no_test_raw_dir"
        echo "Processing NOOP file3."
        tail -n +2 "$no_test_raw_dir/$no_file2" > "$no_test_raw_dir/$no_file3"
    fi
    no_file4="GSM1089291-tbl-1.txt"
    if [ ! -e "$no_test_raw_dir/$no_file4" ]; then
        mkdir -p "$no_test_raw_dir"
        echo "Downloading NOOP file4."
        wget -q -O "$no_test_raw_dir/$no_file4" \
             "$test_data_repo/$no_file4"
    fi
    no_file5="GSM1089291-tbl-1-modified.txt"
    if [ ! -e "$no_test_raw_dir/$no_file5" ]; then
        mkdir -p "$no_test_raw_dir"
        echo "Downloading NOOP file5."
        wget -q -O "$no_test_raw_dir/$no_file5" \
             "$test_data_repo/$no_file5"
    fi

    # Reference files
    no_test_exp_dir="$volume_directory/TEST/NO_OP/EXPECTED"
    no_test_exp_files='gene_converted_GSM557500-tbl-1.txt GSM269747.PCL gene_converted_GSM1234847-tbl-1.txt gene_converted_GSM1089291-tbl-1.txt'
    mkdir -p "$no_test_exp_dir"

    i=1
    for no_test_exp_file in $no_test_exp_files; do
        if ! [ -e "$no_test_exp_dir/$no_test_exp_file" ]; then
            echo "Downloading NOOP expected file$i."
            wget -O "$no_test_exp_dir/$no_test_exp_file" \
                 "$test_data_repo/$no_test_exp_file"
        fi

        i=$(( i + 1 ))
    done
    unset i
fi

if [ -z "$tag" ] || [ "$tag" = "smasher" ] || [ "$tag" = "compendia" ]; then
    # Make sure PCL for test is downloaded from S3
    pcl_name="GSM1237810_T09-1084.PCL"
    pcl_name2="GSM1237812_S97-PURE.PCL"
    pcl_name3="GSM1238108-tbl-1.txt"
    pcl_name4="GSM1487313_liver.PCL"
    pcl_name5="SRP149598_gene_lengthScaledTPM.tsv"
    pcl_name6="GSM1084806-tbl-1.txt"
    pcl_name7="GSM1084807-tbl-1.txt"
    pcl_name_gs1="GSM1084806-tbl-1.txt"
    pcl_name_gs2="GSM1084807-tbl-1.txt"
    pcl_name_ts1="SRR1731761_output_gene_lengthScaledTPM.tsv"
    pcl_name_ts2="SRR1731762_output_gene_lengthScaledTPM.tsv"
    pcl_name_ta1="danio_target.tsv"
    pcl_test_raw_dir="$volume_directory/PCL"
    pcl_test_data_1="$pcl_test_raw_dir/$pcl_name"
    pcl_test_data_2="$pcl_test_raw_dir/$pcl_name2"
    pcl_test_data_3="$pcl_test_raw_dir/$pcl_name3"
    pcl_test_data_4="$pcl_test_raw_dir/$pcl_name4"
    pcl_test_data_5="$pcl_test_raw_dir/$pcl_name5"
    pcl_test_data_6="$pcl_test_raw_dir/$pcl_name6"
    pcl_test_data_7="$pcl_test_raw_dir/$pcl_name7"
    pcl_test_data_gs1="$pcl_test_raw_dir/$pcl_name_gs1"
    pcl_test_data_gs2="$pcl_test_raw_dir/$pcl_name_gs2"
    pcl_test_data_ts1="$pcl_test_raw_dir/$pcl_name_ts1"
    pcl_test_data_ts2="$pcl_test_raw_dir/$pcl_name_ts2"
    pcl_test_data_ta1="$pcl_test_raw_dir/$pcl_name_ta1"
    bad_test_raw_dir="$volume_directory/BADSMASH"
    bad_name="big.PCL"
    bad_name2="small.PCL"
    bad_name3="bad.PCL"
    bad_test_data_1="$bad_test_raw_dir/$bad_name"
    bad_test_data_2="$bad_test_raw_dir/$bad_name2"
    bad_test_data_3="$bad_test_raw_dir/$bad_name3"
    quant_test_raw_dir="$volume_directory/QUANT"
    quant_name="smasher-test-quant.sf"
    quant_test_data_1="$quant_test_raw_dir/$quant_name"
    quant_name_2="smasher-test-truncated-quant.sf"
    quant_test_data_2="$quant_test_raw_dir/$quant_name_2"
    if [ ! -e "$pcl_test_data_1" ]; then
        mkdir -p "$pcl_test_raw_dir"
        echo "Downloading PCL for tests."
        wget -q -O "$pcl_test_data_1" \
             "$test_data_repo/$pcl_name"
    fi
    if [ ! -e "$pcl_test_data_2" ]; then
        echo "Downloading PCL2 for tests."
        wget -q -O "$pcl_test_data_2" \
             "$test_data_repo/$pcl_name2"
    fi
    if [ ! -e "$pcl_test_data_3" ]; then
        echo "Downloading PCL3 for tests."
        wget -q -O "$pcl_test_data_3" \
             "$test_data_repo/$pcl_name3"
    fi
    if [ ! -e "$pcl_test_data_4" ]; then
        echo "Downloading PCL4 for tests."
        wget -q -O "$pcl_test_data_4" \
             "$test_data_repo/$pcl_name4"
    fi
    if [ ! -e "$pcl_test_data_5" ]; then
        echo "Downloading PCL5 for tests."
        wget -q -O "$pcl_test_data_5" \
             "$test_data_repo/$pcl_name5"
    fi
    if [ ! -e "$pcl_test_data_6" ]; then
        echo "Downloading PCL6 for tests."
        wget -q -O "$pcl_test_data_6" \
             "$test_data_repo/$pcl_name6"
    fi
    if [ ! -e "$pcl_test_data_7" ]; then
        echo "Downloading PCL7 for tests."
        wget -q -O "$pcl_test_data_7" \
             "$test_data_repo/$pcl_name7"
    fi
    if [ ! -e "$pcl_test_data_gs1" ]; then
        echo "Downloading PCLGS1 for tests."
        wget -q -O "$pcl_test_data_gs1" \
             "$test_data_repo/$pcl_name_gs1"
    fi
    if [ ! -e "$pcl_test_data_gs2" ]; then
        echo "Downloading PCLGS2 for tests."
        wget -q -O "$pcl_test_data_gs2" \
             "$test_data_repo/$pcl_name_gs2"
    fi
    if [ ! -e "$pcl_test_data_ts1" ]; then
        echo "Downloading PCLTS1 for tests."
        wget -q -O "$pcl_test_data_ts1" \
             "$test_data_repo/$pcl_name_ts1"
    fi
    if [ ! -e "$pcl_test_data_ts2" ]; then
        echo "Downloading PCLTS2 for tests."
        wget -q -O "$pcl_test_data_ts2" \
             "$test_data_repo/$pcl_name_ts2"
    fi
    if [ ! -e "$pcl_test_data_ta1" ]; then
        echo "Downloading PCLTA1 for tests."
        wget -q -O "$pcl_test_data_ta1" \
             "$test_data_repo/$pcl_name_ta1"
    fi
    if [ ! -e "$bad_test_data_1" ]; then
        mkdir -p "$bad_test_raw_dir"
        echo "Downloading Bad PCL for tests."
        wget -q -O "$bad_test_data_1" \
             "$test_data_repo/$bad_name"
    fi
    if [ ! -e "$bad_test_data_2" ]; then
        mkdir -p "$bad_test_raw_dir"
        echo "Downloading Bad PCL for tests."
        wget -q -O "$bad_test_data_2" \
             "$test_data_repo/$bad_name2"
    fi
    if [ ! -e "$bad_test_data_3" ]; then
        mkdir -p "$bad_test_raw_dir"
        echo "Downloading Bad PCL for tests."
        wget -q -O "$bad_test_data_3" \
             "$test_data_repo/$bad_name3"
    fi
    if [ ! -e "$quant_test_data_1" ]; then
        mkdir -p "$quant_test_raw_dir"
        echo "Downloading Quant files for tests."
        wget -q -O "$quant_test_data_1" \
             "$test_data_repo/$quant_name"
    fi
    if [ ! -e "$quant_test_data_2" ]; then
        mkdir -p "$quant_test_raw_dir"
        echo "Downloading Quant files for tests."
        wget -q -O "$quant_test_data_2" \
             "$test_data_repo/$quant_name_2"
    fi
    # Mock out the AWS keys since we use VCR to mock out the request with these
    # as the AWS credentials
    export AWS_ACCESS_KEY_ID=XXX
    export AWS_SECRET_ACCESS_KEY=XXX
fi

if [ -z "$tag" ] || [ "$tag" = "qn" ]; then
    # Make sure PCL for test is downloaded from S3
    qn_name="1.tsv"
    qn_test_raw_dir="$volume_directory/QN"
    qn_test_data_1="$qn_test_raw_dir/$qn_name"
    if [ ! -e "$qn_test_data_1" ]; then
        mkdir -p "$qn_test_raw_dir"
        echo "Downloading QN for tests."
        wget -q -O "$qn_test_data_1" \
             "$test_data_repo/$qn_name"
    fi
    qn_name="2.tsv"
    qn_test_raw_dir="$volume_directory/QN"
    qn_test_data_2="$qn_test_raw_dir/$qn_name"
    if [ ! -e "$qn_test_data_2" ]; then
        mkdir -p "$qn_test_raw_dir"
        echo "Downloading QN for tests."
        wget -q -O "$qn_test_data_2" \
             "$test_data_repo/$qn_name"
    fi
    qn_name="3.tsv"
    qn_test_raw_dir="$volume_directory/QN"
    qn_test_data_3="$qn_test_raw_dir/$qn_name"
    if [ ! -e "$qn_test_data_3" ]; then
        mkdir -p "$qn_test_raw_dir"
        echo "Downloading QN for tests."
        wget -q -O "$qn_test_data_3" \
             "$test_data_repo/$qn_name"
    fi
    qn_name="4.tsv"
    qn_test_raw_dir="$volume_directory/QN"
    qn_test_data_4="$qn_test_raw_dir/$qn_name"
    if [ ! -e "$qn_test_data_4" ]; then
        mkdir -p "$qn_test_raw_dir"
        echo "Downloading QN for tests."
        wget -q -O "$qn_test_data_4" \
             "$test_data_repo/$qn_name"
    fi
    qn_name="5.tsv"
    qn_test_raw_dir="$volume_directory/QN"
    qn_test_data_5="$qn_test_raw_dir/$qn_name"
    if [ ! -e "$qn_test_data_5" ]; then
        mkdir -p "$qn_test_raw_dir"
        echo "Downloading QN for tests."
        wget -q -O "$qn_test_data_5" \
             "$test_data_repo/$qn_name"
    fi
    qn_name="6.tsv"
    qn_test_raw_dir="$volume_directory/QN"
    qn_test_data_6="$qn_test_raw_dir/$qn_name"
    if [ ! -e "$qn_test_data_6" ]; then
        mkdir -p "$qn_test_raw_dir"
        echo "Downloading QN for tests."
        wget -q -O "$qn_test_data_6" \
             "$test_data_repo/$qn_name"
    fi
    qn_name="7.tsv"
    qn_test_raw_dir="$volume_directory/QN"
    qn_test_data_7="$qn_test_raw_dir/$qn_name"
    if [ ! -e "$qn_test_data_7" ]; then
        mkdir -p "$qn_test_raw_dir"
        echo "Downloading QN for tests."
        wget -q -O "$qn_test_data_7" \
             "$test_data_repo/$qn_name"
    fi
fi
if [ -z "$tag" ] || [ "$tag" = "compendia" ]; then
    # Download RNASEQ and MICROARRAY data from prod S3
    micro_list_file="microarray.txt"
    micro_list_dir="$volume_directory/raw/TEST/MICROARRAY"
    if [ ! -e "$micro_list_dir/$micro_list_file" ]; then
        mkdir -p "$micro_list_dir"
        cp "$micro_list_file" "$micro_list_dir/$micro_list_file"
        cd "$micro_list_dir" || exit
        echo "Downloading Microarray Files!"
        wget -q -i "$micro_list_file"
        cd -
    fi
    rnaseq_list_file="rnaseq.txt"
    rnaseq_list_dir="$volume_directory/raw/TEST/RNASEQ"
    if [ ! -e "$rnaseq_list_dir/$rnaseq_list_file" ]; then
        mkdir -p "$rnaseq_list_dir"
        cp "$rnaseq_list_file" "$rnaseq_list_dir/$rnaseq_list_file"
        cd "$rnaseq_list_dir" || exit
        echo "Downloading RNASEQ Files!"
        wget -q -i "$rnaseq_list_file"
        cd -
    fi
    qn_name="danio_target.tsv"
    qn_test_raw_dir="$volume_directory/QN"
    qn_test_data_1="$qn_test_raw_dir/$qn_name"
    if [ ! -e "$qn_test_data_1" ]; then
        mkdir -p "$qn_test_raw_dir"
        echo "Downloading QN for compendia tests."
        wget -q -O "$qn_test_data_1" \
             "$test_data_repo/$qn_name"
    fi
fi

. scripts/common.sh
DB_HOST_IP=$(get_docker_db_ip_address)

# Ensure permissions are set for everything within the test data directory.
chmod -R a+rwX "$volume_directory"

worker_images="salmon transcriptome no_op downloaders smasher illumina agilent affymetrix qn affymetrix_local janitor compendia"

for image in $worker_images; do
    if [ -z "$tag" ] || [ "$tag" = "$image" ]; then
        if [ "$image" = "agilent" ] || [ "$image" = "affymetrix" ]; then
            # Agilent uses the same docker image as Affymetrix
            ./scripts/prepare_image.sh -p -i affymetrix -s workers
            ./scripts/prepare_image.sh -i affymetrix_local -d ccdlstaging
            docker tag ccdlstaging/dr_affymetrix_local:latest ccdlstaging/dr_affymetrix:latest
            image_name=ccdlstaging/dr_affymetrix
        elif [ "$tag" = "qn" ]; then
            ./scripts/prepare_image.sh -i smasher -s workers
            image_name=ccdlstaging/dr_smasher
        elif [ "$tag" = "janitor" ]; then
            ./scripts/prepare_image.sh -i smasher -s workers
            image_name=ccdlstaging/dr_smasher
        else
            ./scripts/prepare_image.sh -i "$image" -s workers
            image_name=ccdlstaging/dr_$image
        fi

        # Strip out tag argument
        # shellcheck disable=2001
        args_without_tag="$(echo "$@" | sed "s/-t $tag//")"
        # shellcheck disable=2086
        test_command="$(run_tests_with_coverage --tag="$image" $args_without_tag)"

        # Only run interactively if we are on a TTY
        if [ -t 1 ]; then
            INTERACTIVE="-i"
        fi

        echo "Running tests with the following command:"
        echo "$test_command"
        docker run -t $INTERACTIVE \
               --add-host=database:"$DB_HOST_IP" \
               --env-file workers/environments/test \
               --env AWS_ACCESS_KEY_ID \
               --env AWS_SECRET_ACCESS_KEY \
               --volume "$volume_directory":/home/user/data_store \
               --memory=5G \
               "$image_name" bash -c "$test_command"
    fi
done
