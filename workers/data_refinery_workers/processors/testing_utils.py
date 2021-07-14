from django.test import TestCase

import pandas as pd
import scipy

from data_refinery_workers.processors.utils import squish_duplicates


def assertMostlyAgrees(test_case: TestCase, expected_data: pd.Series, actual_data: pd.Series):
    """Checks to make sure that the expected data and the actual data are mostly
    the same, i.e. they have mostly the same genes and for the genes they have
    in common their spearman correlation is >0.99.

    We are only checking for approximate equality because output varies slightly
    between runs and between bioconductor versions."""

    # Make sure that the genes haven't changed too drastically between runs.
    # If this fails, it's probably not the end of the world but probably
    # something we should know about.
    test_case.assertGreater(
        len(set(expected_data.index) & set(actual_data.index)),
        0.95 * min(len(set(expected_data.index)), len(set(actual_data.index))),
    )

    expected_df = squish_duplicates(pd.DataFrame({"expected_values": expected_data}))
    actual_df = squish_duplicates(pd.DataFrame({"actual_values": actual_data}))

    (rho, _) = scipy.stats.spearmanr(expected_df.join(actual_df, how="inner"))
    test_case.assertGreater(rho, 0.99)


class ProcessorJobTestCaseMixin:
    def assertSucceeded(self, pj):
        pj.refresh_from_db()

        if pj.success:
            return

        if pj.failure_reason is not None:
            msg = f"Processor job failed with reason '{pj.failure_reason}'"
        else:
            msg = f"Processor job failed without a given reason"

        raise self.failureException(msg)

    def assertFailed(self, pj, expected_reason=""):
        pj.refresh_from_db()

        if not pj.success and expected_reason in pj.failure_reason:
            return

        if pj.success:
            raise self.failureException("Expected processor job to fail, but it succeeded")

        raise self.failureException(
            "Processor job failed, but for an incorrect reason.\n"
            + f"We expected the reason to contain '{expected_reason}' but the actual reason was '{pj.failure_reason}'"
        )
