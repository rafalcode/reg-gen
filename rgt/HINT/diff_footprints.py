# Import
import os
import numpy as np
import pysam
from pysam import Samfile
from pysam import Fastafile
from math import log, ceil, floor
from Bio import motifs
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import pyx
from scipy import stats

# Internal
from ..Util import AuxiliaryFunctions, GenomeData
from rgt.GenomicRegionSet import GenomicRegionSet
from biasTable import BiasTable
from signalProcessing import GenomicSignal
from pileupRegion import PileupRegion

"""
Perform differential footprints analysis based on the prediction.

Authors: Eduardo G. Gusmao, Zhijian Li
"""

dic = {"A": 0, "C": 1, "G": 2, "T": 3}

###################################################################################################
# Classes
###################################################################################################

class DiffFootprints:

    def __init__(self, organism, mpbs_file, reads_file1, reads_file2, bias_table1, bias_table2,
                 window_size, motif_ext, min_value, initial_clip, downstream_ext, upstream_ext,
                 forward_shift, reverse_shift, k_nb, housekeeping_genes, output_location, output_prefix):
        self.organism = organism
        self.mpbs_file = mpbs_file
        self.reads_file1 = reads_file1
        self.reads_file2 = reads_file2
        self.bias_table1 = bias_table1
        self.bias_table2 = bias_table2
        self.window_size = window_size
        self.motif_ext = motif_ext
        self.min_value = min_value
        self.initial_clip = initial_clip
        self.downstream_ext = downstream_ext
        self.upstream_ext = upstream_ext
        self.forward_shift = forward_shift
        self.reverse_shift = reverse_shift
        self.k_nb = k_nb
        self.housekeeping_genes = housekeeping_genes
        self.output_location = output_location
        self.output_prefix = output_prefix

    def bias_correction(self, bam, bias_table, genome_file_name, chrName, start, end,
                        forward_shift, reverse_shift, factor):
        # Parameters
        window = 50
        defaultKmerValue = 1.0

        # Initialization
        fastaFile = Fastafile(genome_file_name)
        fBiasDict = bias_table[0]
        rBiasDict = bias_table[1]
        k_nb = len(fBiasDict.keys()[0])
        p1 = start
        p2 = end
        p1_w = p1 - (window / 2)
        p2_w = p2 + (window / 2)
        p1_wk = p1_w - int(floor(k_nb / 2.))
        p2_wk = p2_w + int(ceil(k_nb / 2.))

        # Raw counts
        nf = [0.0] * (p2_w - p1_w)
        nr = [0.0] * (p2_w - p1_w)
        for read in bam.fetch(chrName, p1_w, p2_w):
            if (not read.is_reverse):
                cut_site = read.pos + forward_shift
                if cut_site >= p1_w and cut_site < p2_w:
                    nf[cut_site - p1_w] += 1.0
            else:
                cut_site = read.aend + reverse_shift - 1
                if cut_site >= p1_w and cut_site < p2_w:
                    nr[cut_site - p1_w] += 1.0

        nf = [i * factor for i in nf]
        nr = [i * factor for i in nr]
        # Std-based clipping to avoid spurious high counts
        # mean = np.array(nf).mean()
        # std = np.array(nf).std()
        # nf = [min(e, mean + (10 * std)) for e in nf]

        # mean = np.array(nr).mean()
        # std = np.array(nr).std()
        # nr = [min(e, mean + (10 * std)) for e in nr]

        # Smoothed counts
        Nf = []
        Nr = []
        fSum = sum(nf[:window])
        rSum = sum(nr[:window])
        fLast = nf[0]
        rLast = nr[0]
        for i in range((window / 2), len(nf) - (window / 2)):
            Nf.append(fSum)
            Nr.append(rSum)
            fSum -= fLast
            fSum += nf[i + (window / 2)]
            fLast = nf[i - (window / 2) + 1]
            rSum -= rLast
            rSum += nr[i + (window / 2)]
            rLast = nr[i - (window / 2) + 1]

        # Fetching sequence
        currStr = str(fastaFile.fetch(chrName, p1_wk, p2_wk - 1)).upper()
        currRevComp = AuxiliaryFunctions.revcomp(str(fastaFile.fetch(chrName, p1_wk + 1,
                                                                     p2_wk)).upper())

        # Iterating on sequence to create signal
        af = []
        ar = []
        for i in range(int(ceil(k_nb / 2.)), len(currStr) - int(floor(k_nb / 2)) + 1):
            fseq = currStr[i - int(floor(k_nb / 2.)):i + int(ceil(k_nb / 2.))]
            rseq = currRevComp[len(currStr) - int(ceil(k_nb / 2.)) - i:len(currStr) + int(floor(k_nb / 2.)) - i]
            try:
                af.append(fBiasDict[fseq])
            except Exception:
                af.append(defaultKmerValue)
            try:
                ar.append(rBiasDict[rseq])
            except Exception:
                ar.append(defaultKmerValue)

        # Calculating bias and writing to wig file
        fSum = sum(af[:window])
        rSum = sum(ar[:window])
        fLast = af[0]
        rLast = ar[0]
        bias_corrected_signal = []
        bias_corrected_tc = []
        for i in range((window / 2), len(af) - (window / 2)):
            nhatf = Nf[i - (window / 2)] * (af[i] / fSum)
            nhatr = Nr[i - (window / 2)] * (ar[i] / rSum)
            zf = log(nf[i] + 1) - log(nhatf + 1)
            zr = log(nr[i] + 1) - log(nhatr + 1)
            bias_corrected_signal.append(zf + zr)
            #bias_corrected_tc.append(nf[i] + nr[i])
            bias_corrected_tc.append(nf[i] / af[i] + nr[i] / ar[i])
            fSum -= fLast
            fSum += af[i + (window / 2)]
            fLast = af[i - (window / 2) + 1]
            rSum -= rLast
            rSum += ar[i + (window / 2)]
            rLast = ar[i - (window / 2) + 1]

        # Termination
        fastaFile.close()
        return bias_corrected_signal, bias_corrected_tc

    def get_stats(self, bam, bias_table, genome_file_name, chrName, start, end, motif_length, factor):

        bc_signal, bias_corrected_tc = self.bias_correction(bam, bias_table, genome_file_name, chrName, start, end,
                         self.forward_shift, self.reverse_shift, factor)

        stdzSignal = [e + self.min_value for e in bc_signal]

        # Evaluating protection score
        signal_half_len = len(bc_signal) / 2
        motif_half_len = motif_length / 2
        nc = sum(stdzSignal[signal_half_len - motif_half_len:signal_half_len + motif_half_len])
        nr = sum(stdzSignal[signal_half_len + motif_half_len:signal_half_len + motif_half_len + motif_length])
        nl = sum(stdzSignal[signal_half_len - motif_half_len - motif_length:signal_half_len - motif_half_len])

        spr = (nr - nc) / motif_length + (nl - nc) / motif_length
        fos = (nr + 1) / (nc + 1) + (nl + 1) / (nc + 1)

        # nc_mean = sum(bc_signal[signal_half_len - motif_half_len:signal_half_len + motif_half_len]) / motif_length
        # nl_mean = sum(bc_signal[:signal_half_len - motif_half_len]) / len(bc_signal[:signal_half_len - motif_half_len])
        # nr_mean = sum(bc_signal[signal_half_len + motif_half_len:]) / len(bc_signal[signal_half_len + motif_half_len:])
        #
        # spr = nl_mean + nr_mean - 2 * nc_mean
        # fos = (nl_mean + 1) / (nc_mean + 1) + (nr_mean + 1) / (nc_mean + 1)

        #nr = sum(bias_corrected_tc[signal_half_len + motif_half_len:signal_half_len + motif_half_len + motif_length])
        #nl = sum(bias_corrected_tc[signal_half_len - motif_half_len - motif_length:signal_half_len - motif_half_len])
        #tc = (nr + nl) / (2 * motif_length)
        nr = sum(bias_corrected_tc[signal_half_len + motif_half_len:]) / len(
             bias_corrected_tc[signal_half_len + motif_half_len:])
        nl = sum(bias_corrected_tc[:signal_half_len - motif_half_len]) / len(
             bias_corrected_tc[:signal_half_len - motif_half_len])
        tc = (nr + nl) / 2

        return spr, tc, fos, np.array(bc_signal), np.array(bias_corrected_tc)

    def diff(self):
        mpbs = GenomicRegionSet("Motif Predicted Binding Sites")
        mpbs.read_bed(self.mpbs_file)

        mpbs_name_list = list(set(mpbs.get_names()))

        genome_data = GenomeData(self.organism)
        fasta = Fastafile(genome_data.get_genome())

        bias_table1 = None
        bias_table2 = None
        if self.bias_table1:
            table_list = self.bias_table1.split(",")
            bias_table1 = BiasTable().load_table(table_file_name_F=table_list[0], table_file_name_R=table_list[1])
        if self.bias_table2:
            table_list = self.bias_table2.split(",")
            bias_table2 = BiasTable().load_table(table_file_name_F=table_list[0], table_file_name_R=table_list[1])

        # Compute the reads number ratio between two bam file
        ct1 = reduce(lambda x, y: x + y, [int(l.rstrip('\n').split('\t')[2]) for l in pysam.idxstats(self.reads_file1)])
        ct2 = reduce(lambda x, y: x + y, [int(l.rstrip('\n').split('\t')[2]) for l in pysam.idxstats(self.reads_file2)])

        if (ct1 > ct2):
            factor1 = 1.0
            factor2 = ct1 / float(ct2)
        else:
            factor2 = 1.0
            factor1 = ct2 / float(ct1)

        self.output_factor(factor1, factor2)
        # Iterating on MPBSs

        bam1 = Samfile(self.reads_file1, "rb")
        bam2 = Samfile(self.reads_file2, "rb")
        for mpbs_name in mpbs_name_list:
            pwm_dict = dict([("A", [0.0] * self.window_size), ("C", [0.0] * self.window_size),
                             ("G", [0.0] * self.window_size), ("T", [0.0] * self.window_size),
                             ("N", [0.0] * self.window_size)])

            mpbs_regions = mpbs.by_names([mpbs_name])
            spr1_list = list()
            spr2_list = list()
            tc1_list = list()
            tc2_list = list()
            fc1_list = list()
            fc2_list = list()
            spr1 = 0.0
            spr2 = 0.0
            dspr = 0.0
            tc1 = 0
            tc2 = 0
            dtc = 0
            fc1 = 0
            fc2 = 0
            dfc = 0
            corrected_signal1 = []
            corrected_signal2 = []
            dcorrected_signal = []
            tc_corrected_signal1 = []
            tc_corrected_signal2 = []
            dtc_corrected_signal = []
            counter = 0.0
            for region in mpbs_regions:
                length = region.final - region.initial
                mid = (region.final + region.initial) / 2
                p1 = max(mid - self.window_size / 2, 0)
                p2 = mid + self.window_size / 2

                sp1, tc_1, fc_1, corrected_signal_1, tc_corrected_signal_1 = \
                    self.get_stats(bam=bam1, bias_table=bias_table1,
                                   genome_file_name=genome_data.get_genome(),
                                   chrName=region.chrom, start=p1, end=p2, motif_length=length, factor=factor1)

                sp2, tc_2, fc_2, corrected_signal_2, tc_corrected_signal_2 = \
                    self.get_stats(bam=bam2, bias_table=bias_table2,
                                   genome_file_name=genome_data.get_genome(),
                                   chrName=region.chrom, start=p1, end=p2, motif_length=length, factor=factor2)
                spr1 += sp1
                spr2 += sp2
                #dspr += max(0, sp2) - max(0, sp1)
                dspr += sp2 - sp1
                tc1 += tc_1
                tc2 += tc_2
                dtc += tc_2 - tc_1
                fc1 += fc_1
                fc2 += fc_2
                dfc += fc_2 - fc_1

                spr1_list.append(sp1)
                spr2_list.append(sp2)
                tc1_list.append(tc_1)
                fc1_list.append(fc_1)

                tc2_list.append(tc_2)
                fc2_list.append(fc_2)

                if (region.orientation == "-"):
                    corrected_signal_1 = corrected_signal_1[::-1]
                    corrected_signal_2 = corrected_signal_2[::-1]
                    tc_corrected_signal_1 = tc_corrected_signal_1[::-1]
                    tc_corrected_signal_2 = tc_corrected_signal_2[::-1]

                if (len(corrected_signal1) == 0):
                    corrected_signal1 = corrected_signal_1
                    corrected_signal2 = corrected_signal_2
                    dcorrected_signal = corrected_signal_2 - corrected_signal1
                    tc_corrected_signal1 = tc_corrected_signal_1
                    tc_corrected_signal2 = tc_corrected_signal_2
                    dtc_corrected_signal = tc_corrected_signal_2 - tc_corrected_signal_1
                else:
                    corrected_signal1 += corrected_signal_1
                    corrected_signal2 += corrected_signal_2
                    dcorrected_signal += corrected_signal_2 - corrected_signal1
                    tc_corrected_signal1 += tc_corrected_signal_1
                    tc_corrected_signal2 += tc_corrected_signal_2
                    dtc_corrected_signal += tc_corrected_signal_2 - tc_corrected_signal_1
                counter += 1.0

                # Update pwm
                aux_plus = 1
                dna_seq = str(fasta.fetch(region.chrom, p1, p2)).upper()
                if (region.final - region.initial) % 2 == 0:
                    aux_plus = 0
                dna_seq_rev = AuxiliaryFunctions.revcomp(str(fasta.fetch(region.chrom,
                                                                         p1 + aux_plus, p2 + aux_plus)).upper())
                if region.orientation == "+":
                    for i in range(0, len(dna_seq)):
                        pwm_dict[dna_seq[i]][i] += 1
                elif region.orientation == "-":
                    for i in range(0, len(dna_seq_rev)):
                        pwm_dict[dna_seq_rev[i]][i] += 1

            # Updating protection
            if (counter > 0):
                protmean1 = spr1 / counter
                protmean2 = spr2 / counter
                difprotmean = dspr / counter
                tcmean1 = tc1 / counter
                tcmean2 = tc2 / counter
                diftcmean = dtc / counter
                fcmean1 = fc1 / counter
                fcmean2 = fc2 / counter
                diffcmean = dfc / counter
                corrected_signal1_mean = corrected_signal1 / counter
                corrected_signal2_mean = corrected_signal2 / counter
                dcorrected_signal_mean = dcorrected_signal / counter
                tccorrectedSignalMean1 = tc_corrected_signal1 / counter
                tccorrectedSignalMean2 = tc_corrected_signal2 / counter
                dtc_corrected_signal = dtc_corrected_signal / counter

                # Kolmogorov-Smirnov test
                spr_ks_stat, spr_ks_pvalue = stats.ks_2samp(np.array(spr2_list), np.array(spr1_list))
                tc_ks_stat, tc_ks_pvalue = stats.ks_2samp(np.array(tc2_list), np.array(tc1_list))
                fc_ks_stat, fc_ks_pvalue = stats.ks_2samp(np.array(fc2_list), np.array(fc1_list))

                # Calculates the T-test for the mean of ONE group of scores
                dspr = np.subtract(np.array(spr2_list), np.array(spr1_list))
                dtc = np.subtract(np.array(tc2_list), np.array(tc1_list))
                dfc = np.subtract(np.array(fc2_list), np.array(fc1_list))

                spr_t_1samp_stat, spr_t_1samp_pvalue = stats.ttest_1samp(dspr, 0.0)
                tc_t_1samp_stat, tc_t_1samp_pvalue = stats.ttest_1samp(dtc, 0.0)
                fc_t_1samp_stat, fc_t_1samp_pvalue = stats.ttest_1samp(dfc, 0.0)

                # Calculates the T-test for the means of two independent samples of scores.
                spr_ttest_ind_stat, spr_ttest_ind_pvalue = stats.ttest_ind(np.array(spr2_list), np.array(spr1_list))
                tc_ttest_ind_stat, tc_ttest_ind_pvalue = stats.ttest_ind(np.array(tc2_list), np.array(tc1_list))
                fc_ttest_ind_stat, fc_ttest_ind_pvalue = stats.ttest_ind(np.array(fc2_list), np.array(fc1_list))

                res = [protmean1, protmean2, difprotmean,
                       spr_ks_stat, spr_ks_pvalue, spr_t_1samp_stat, spr_t_1samp_pvalue, spr_ttest_ind_stat,
                       spr_ttest_ind_pvalue,
                       tcmean1, tcmean2, diftcmean,
                       tc_ks_stat, tc_ks_pvalue, tc_t_1samp_stat, tc_t_1samp_pvalue, tc_ttest_ind_stat,
                       tc_ttest_ind_pvalue,
                       fcmean1, fcmean2, diffcmean,
                       fc_ks_stat, fc_ks_pvalue, fc_t_1samp_stat, fc_t_1samp_pvalue, fc_ttest_ind_stat,
                       fc_ttest_ind_pvalue,
                       counter]
                output_file = os.path.join(self.output_location, "{}.txt".format(self.output_prefix))
                f = open(output_file, "a")
                f.write("\t".join([mpbs_name] + [str(i) for i in res]))
                f.write("\n")
                f.close()

                self.plot(mpbs_name, tccorrectedSignalMean1, tccorrectedSignalMean2,
                          pwm_dict, self.output_location, self.output_prefix)

    # def diff1(self):
    #     mpbs = GenomicRegionSet("Motif Predicted Binding Sites")
    #     mpbs.read_bed(self.mpbs_file)
    #
    #     mpbs_name_list = list(set(mpbs.get_names()))
    #
    #     genome_data = GenomeData(self.organism)
    #     fasta = Fastafile(genome_data.get_genome())
    #
    #     bias_table1 = None
    #     bias_table2 = None
    #     if self.bias_table1:
    #         table_list = self.bias_table1.split(",")
    #         bias_table1 = BiasTable().load_table(table_file_name_F=table_list[0], table_file_name_R=table_list[1])
    #     if self.bias_table2:
    #         table_list = self.bias_table2.split(",")
    #         bias_table2 = BiasTable().load_table(table_file_name_F=table_list[0], table_file_name_R=table_list[1])
    #
    #     # Iterating on MPBSs
    #     for mpbs_name in mpbs_name_list:
    #         pwm_dict = dict([("A", [0.0] * self.window_size), ("C", [0.0] * self.window_size),
    #                          ("G", [0.0] * self.window_size), ("T", [0.0] * self.window_size),
    #                          ("N", [0.0] * self.window_size)])
    #
    #         mpbs_regions = mpbs.by_names([mpbs_name])
    #
    #         fos1_list = list()
    #         fos2_list = list()
    #         counter = 0.0
    #         for region in mpbs_regions:
    #             mid = (region.final + region.initial) / 2
    #             p1 = max(mid - self.window_size / 2, 0)
    #             p2 = mid + self.window_size / 2
    #
    #             fos1 = self.get_stats(reads_file=self.reads_file1, bias_table=bias_table1,
    #                                genome_file_name=genome_data.get_genome(),
    #                                chrName=region.chrom, start=p1, end=p2, factor=1)
    #
    #             fos2 = self.get_stats(reads_file=self.reads_file2, bias_table=bias_table2,
    #                                genome_file_name=genome_data.get_genome(),
    #                                chrName=region.chrom, start=p1, end=p2, factor=1)
    #
    #             fos1_list.append(fos1)
    #             fos2_list.append(fos2)
    #             counter += 1.0
    #
    #         # Updating protection
    #         if (counter > 0):
    #             mean_fos1 = np.mean(fos1_list)
    #             std_fos1 = np.std(fos1_list)
    #             mean_fos2 = np.mean(fos2_list)
    #             std_fos2 = np.std(fos2_list)
    #
    #             output_file = os.path.join(self.output_location, self.output_prefix, "{}.txt".format(mpbs_name))
    #             f = open(output_file, "w")
    #             f.write("\t".join(["FOS1"] + [str(i) for i in fos1_list]))
    #             f.write("\n")
    #             f.write("\t".join(["FOS2"] + [str(i) for i in fos2_list]))
    #             f.close()
    #
    #             stat, pvalue = stats.ttest_ind(np.array(fos1_list), np.array(fos2_list))
    #             res = [mean_fos1, std_fos1, mean_fos2, std_fos2, stat, pvalue, counter]
    #             output_file = os.path.join(self.output_location, "{}.txt".format(self.output_prefix))
    #             f = open(output_file, "a")
    #             f.write("\t".join([mpbs_name] + [str(i) for i in res]))
    #             f.write("\n")
    #             f.close()

    def diff1(self):
        mpbs = GenomicRegionSet("Motif Predicted Binding Sites")
        mpbs.read_bed(self.mpbs_file)

        mpbs_name_list = list(set(mpbs.get_names()))

        genome_data = GenomeData(self.organism)
        fasta = Fastafile(genome_data.get_genome())

        bias_table1 = None
        bias_table2 = None
        if self.bias_table1:
            table_list = self.bias_table1.split(",")
            bias_table1 = BiasTable().load_table(table_file_name_F=table_list[0], table_file_name_R=table_list[1])
        if self.bias_table2:
            table_list = self.bias_table2.split(",")
            bias_table2 = BiasTable().load_table(table_file_name_F=table_list[0], table_file_name_R=table_list[1])

        # Compute the normalization factor based on Housekeeping Gene Normalization Approach
        mpbs_regions = mpbs.by_names(["MA0080.4.SPI1"])
        total_tc1 = 0
        total_tc2 = 0
        signal1 = GenomicSignal(self.reads_file1)
        signal2 = GenomicSignal(self.reads_file2)
        for region in mpbs_regions:
            total_tc1 += signal1.get_tag_count(ref=region.chrom, start=region.initial, end=region.final,
                                              downstream_ext=self.downstream_ext, upstream_ext=self.upstream_ext,
                                              forward_shift=self.forward_shift, reverse_shift=self.reverse_shift)
            total_tc2 += signal2.get_tag_count(ref=region.chrom, start=region.initial, end=region.final,
                                              downstream_ext=self.downstream_ext, upstream_ext=self.upstream_ext,
                                              forward_shift=self.forward_shift, reverse_shift=self.reverse_shift)

        mean_tc = (total_tc1 + total_tc2) / 2.0
        factor1 = mean_tc / total_tc1
        factor2 = mean_tc / total_tc2

        self.output_factor(factor1, factor2)

        # Iterating on MPBSs
        bam1 = Samfile(self.reads_file1, "rb")
        bam2 = Samfile(self.reads_file2, "rb")
        for mpbs_name in mpbs_name_list:
            pwm_dict = dict([("A", [0.0] * self.window_size), ("C", [0.0] * self.window_size),
                             ("G", [0.0] * self.window_size), ("T", [0.0] * self.window_size),
                             ("N", [0.0] * self.window_size)])

            mpbs_regions = mpbs.by_names([mpbs_name])
            spr1 = 0.0
            spr2 = 0.0
            dspr = 0.0
            tc1 = 0
            tc2 = 0
            dtc = 0
            fc1 = 0
            fc2 = 0
            dfc = 0
            corrected_signal1 = []
            corrected_signal2 = []
            dcorrected_signal = []
            tc_corrected_signal1 = []
            tc_corrected_signal2 = []
            dtc_corrected_signal = []
            counter = 0.0
            for region in mpbs_regions:
                length = region.final - region.initial
                mid = (region.final + region.initial) / 2
                p1 = max(mid - self.window_size / 2, 0)
                p2 = mid + self.window_size / 2

                sp1, tc_1, fc_1, corrected_signal_1, tc_corrected_signal_1 = \
                    self.get_stats(bam=bam1, bias_table=bias_table1,
                                   genome_file_name=genome_data.get_genome(),
                                   chrName=region.chrom, start=p1, end=p2, motif_length=length, factor=factor1)

                sp2, tc_2, fc_2, corrected_signal_2, tc_corrected_signal_2 = \
                    self.get_stats(bam=bam2, bias_table=bias_table2,
                                   genome_file_name=genome_data.get_genome(),
                                   chrName=region.chrom, start=p1, end=p2, motif_length=length, factor=factor2)
                spr1 += sp1
                spr2 += sp2
                #dspr += max(0, sp2) - max(0, sp1)
                dspr += sp2 - sp1
                tc1 += tc_1
                tc2 += tc_2
                dtc += tc_2 - tc_1
                fc1 += fc_1
                fc2 += fc_2
                dfc += fc_2 - fc_1

                if (region.orientation == "-"):
                    corrected_signal_1 = corrected_signal_1[::-1]
                    corrected_signal_2 = corrected_signal_2[::-1]
                    tc_corrected_signal_1 = tc_corrected_signal_1[::-1]
                    tc_corrected_signal_2 = tc_corrected_signal_2[::-1]

                if (len(corrected_signal1) == 0):
                    corrected_signal1 = corrected_signal_1
                    corrected_signal2 = corrected_signal_2
                    dcorrected_signal = corrected_signal_2 - corrected_signal1
                    tc_corrected_signal1 = tc_corrected_signal_1
                    tc_corrected_signal2 = tc_corrected_signal_2
                    dtc_corrected_signal = tc_corrected_signal_2 - tc_corrected_signal_1
                else:
                    corrected_signal1 += corrected_signal_1
                    corrected_signal2 += corrected_signal_2
                    dcorrected_signal += corrected_signal_2 - corrected_signal1
                    tc_corrected_signal1 += tc_corrected_signal_1
                    tc_corrected_signal2 += tc_corrected_signal_2
                    dtc_corrected_signal += tc_corrected_signal_2 - tc_corrected_signal_1
                counter += 1.0

                # Update pwm
                aux_plus = 1
                dna_seq = str(fasta.fetch(region.chrom, p1, p2)).upper()
                if (region.final - region.initial) % 2 == 0:
                    aux_plus = 0
                dna_seq_rev = AuxiliaryFunctions.revcomp(str(fasta.fetch(region.chrom,
                                                                         p1 + aux_plus, p2 + aux_plus)).upper())
                if region.orientation == "+":
                    for i in range(0, len(dna_seq)):
                        pwm_dict[dna_seq[i]][i] += 1
                elif region.orientation == "-":
                    for i in range(0, len(dna_seq_rev)):
                        pwm_dict[dna_seq_rev[i]][i] += 1

            # Updating protection
            if (counter > 0):
                protmean1 = spr1 / counter
                protmean2 = spr2 / counter
                difprotmean = dspr / counter
                tcmean1 = tc1 / counter
                tcmean2 = tc2 / counter
                diftcmean = dtc / counter
                fcmean1 = fc1 / counter
                fcmean2 = fc2 / counter
                diffcmean = dfc / counter
                corrected_signal1_mean = corrected_signal1 / counter
                corrected_signal2_mean = corrected_signal2 / counter
                dcorrected_signal_mean = dcorrected_signal / counter
                tccorrectedSignalMean1 = tc_corrected_signal1 / counter
                tccorrectedSignalMean2 = tc_corrected_signal2 / counter
                dtc_corrected_signal = dtc_corrected_signal / counter

                res = [protmean1, protmean2, difprotmean, tcmean1, tcmean2, diftcmean,
                       fcmean1, fcmean2, diffcmean, counter]
                output_file = os.path.join(self.output_location, "{}.txt".format(self.output_prefix))
                f = open(output_file, "a")
                f.write("\t".join([mpbs_name] + [str(i) for i in res]))
                f.write("\n")
                f.close()

                self.plot(mpbs_name, tccorrectedSignalMean1, tccorrectedSignalMean2,
                          pwm_dict, self.output_location, self.output_prefix)

    def diff2(self, factor1, factor2):
        mpbs = GenomicRegionSet("Motif Predicted Binding Sites")
        mpbs.read_bed(self.mpbs_file)

        mpbs_name_list = list(set(mpbs.get_names()))

        genome_data = GenomeData(self.organism)
        fasta = Fastafile(genome_data.get_genome())

        bias_table1 = None
        bias_table2 = None
        if self.bias_table1:
            table_list = self.bias_table1.split(",")
            bias_table1 = BiasTable().load_table(table_file_name_F=table_list[0], table_file_name_R=table_list[1])
        if self.bias_table2:
            table_list = self.bias_table2.split(",")
            bias_table2 = BiasTable().load_table(table_file_name_F=table_list[0], table_file_name_R=table_list[1])

        # Compute the number of total non-mitochondrial tag counts located in footprints
        if factor1 == None and factor2 == None:
            total_tc1 = 0
            total_tc2 = 0
            signal1 = GenomicSignal(self.reads_file1)
            signal2 = GenomicSignal(self.reads_file2)
            for region in mpbs:
                mid = (region.final + region.initial) / 2
                p1 = max(mid - self.window_size / 2, 0)
                p2 = mid + self.window_size / 2

                total_tc1 += signal1.get_tag_count(ref=region.chrom, start=p1, end=p2,
                                                   downstream_ext=self.downstream_ext, upstream_ext=self.upstream_ext,
                                                   forward_shift=self.forward_shift, reverse_shift=self.reverse_shift)
                total_tc2 += signal2.get_tag_count(ref=region.chrom, start=p1, end=p2,
                                                   downstream_ext=self.downstream_ext, upstream_ext=self.upstream_ext,
                                                   forward_shift=self.forward_shift, reverse_shift=self.reverse_shift)


            if (total_tc1 > total_tc2):
                factor1 = 1.0
                factor2 = total_tc1 / float(total_tc2)
            else:
                factor2 = 1.0
                factor1 = total_tc2 / float(total_tc1)

            self.output_factor(factor1, factor2)

        # Iterating on MPBSs
        bam1 = Samfile(self.reads_file1, "rb")
        bam2 = Samfile(self.reads_file2, "rb")

        prot_dict1 = dict()
        tc_dict1 = dict()
        prot_dict2 = dict()
        tc_dict2 = dict()
        for mpbs_name in mpbs_name_list:
            pwm_dict = dict([("A", [0.0] * self.window_size), ("C", [0.0] * self.window_size),
                             ("G", [0.0] * self.window_size), ("T", [0.0] * self.window_size),
                             ("N", [0.0] * self.window_size)])

            mpbs_regions = mpbs.by_names([mpbs_name])
            prot_dict1[mpbs_name] = list()
            prot_dict2[mpbs_name] = list()
            tc_dict1[mpbs_name] = list()
            tc_dict2[mpbs_name] = list()

            # spr1_list = list()
            # spr2_list = list()
            # tc1_list = list()
            # tc2_list = list()
            # fc1_list = list()
            # fc2_list = list()
            # spr1 = 0.0
            # spr2 = 0.0
            # dspr = 0.0
            # tc1 = 0
            # tc2 = 0
            # dtc = 0
            # fc1 = 0
            # fc2 = 0
            # dfc = 0
            corrected_signal1 = []
            corrected_signal2 = []
            dcorrected_signal = []
            tc_corrected_signal1 = []
            tc_corrected_signal2 = []
            dtc_corrected_signal = []
            counter = 0.0
            for region in mpbs_regions:
                length = region.final - region.initial
                mid = (region.final + region.initial) / 2
                p1 = max(mid - self.window_size / 2, 0)
                p2 = mid + self.window_size / 2

                sp1, tc_1, fc_1, corrected_signal_1, tc_corrected_signal_1 = \
                    self.get_stats(bam=bam1, bias_table=bias_table1,
                                   genome_file_name=genome_data.get_genome(),
                                   chrName=region.chrom, start=p1, end=p2, motif_length=length, factor=factor1)

                sp2, tc_2, fc_2, corrected_signal_2, tc_corrected_signal_2 = \
                    self.get_stats(bam=bam2, bias_table=bias_table2,
                                   genome_file_name=genome_data.get_genome(),
                                   chrName=region.chrom, start=p1, end=p2, motif_length=length, factor=factor2)
                # spr1 += sp1
                # spr2 += sp2
                # dspr += sp2 - sp1
                # tc1 += tc_1
                # tc2 += tc_2
                # dtc += tc_2 - tc_1
                # fc1 += fc_1
                # fc2 += fc_2
                # dfc += fc_2 - fc_1

                prot_dict1[mpbs_name].append(sp1)
                prot_dict2[mpbs_name].append(sp2)
                tc_dict1[mpbs_name].append(tc_1)
                tc_dict2[mpbs_name].append(tc_2)

                # spr1_list.append(sp1)
                # spr2_list.append(sp2)
                # tc1_list.append(tc_1)
                # fc1_list.append(fc_1)
                # tc2_list.append(tc_2)
                # fc2_list.append(fc_2)

                if (region.orientation == "-"):
                    corrected_signal_1 = corrected_signal_1[::-1]
                    corrected_signal_2 = corrected_signal_2[::-1]
                    tc_corrected_signal_1 = tc_corrected_signal_1[::-1]
                    tc_corrected_signal_2 = tc_corrected_signal_2[::-1]

                if (len(corrected_signal1) == 0):
                    corrected_signal1 = corrected_signal_1
                    corrected_signal2 = corrected_signal_2
                    dcorrected_signal = corrected_signal_2 - corrected_signal1
                    tc_corrected_signal1 = tc_corrected_signal_1
                    tc_corrected_signal2 = tc_corrected_signal_2
                    dtc_corrected_signal = tc_corrected_signal_2 - tc_corrected_signal_1
                else:
                    corrected_signal1 += corrected_signal_1
                    corrected_signal2 += corrected_signal_2
                    dcorrected_signal += corrected_signal_2 - corrected_signal1
                    tc_corrected_signal1 += tc_corrected_signal_1
                    tc_corrected_signal2 += tc_corrected_signal_2
                    dtc_corrected_signal += tc_corrected_signal_2 - tc_corrected_signal_1
                counter += 1.0

                # Update pwm
                aux_plus = 1
                dna_seq = str(fasta.fetch(region.chrom, p1, p2)).upper()
                if (region.final - region.initial) % 2 == 0:
                    aux_plus = 0
                dna_seq_rev = AuxiliaryFunctions.revcomp(str(fasta.fetch(region.chrom,
                                                                         p1 + aux_plus, p2 + aux_plus)).upper())
                if region.orientation == "+":
                    for i in range(0, len(dna_seq)):
                        pwm_dict[dna_seq[i]][i] += 1
                elif region.orientation == "-":
                    for i in range(0, len(dna_seq_rev)):
                        pwm_dict[dna_seq_rev[i]][i] += 1

            # Updating protection
            if (counter > 0):
                # protmean1 = spr1 / counter
                # protmean2 = spr2 / counter
                # difprotmean = dspr / counter
                # tcmean1 = tc1 / counter
                # tcmean2 = tc2 / counter
                # diftcmean = dtc / counter
                # fcmean1 = fc1 / counter
                # fcmean2 = fc2 / counter
                # diffcmean = dfc / counter
                # corrected_signal1_mean = corrected_signal1 / counter
                # corrected_signal2_mean = corrected_signal2 / counter
                # dcorrected_signal_mean = dcorrected_signal / counter
                tccorrectedSignalMean1 = tc_corrected_signal1 / counter
                tccorrectedSignalMean2 = tc_corrected_signal2 / counter
                # dtc_corrected_signal = dtc_corrected_signal / counter

                # Kolmogorov-Smirnov test
                # spr_ks_stat, spr_ks_pvalue = stats.ks_2samp(np.array(spr2_list), np.array(spr1_list))
                # tc_ks_stat, tc_ks_pvalue = stats.ks_2samp(np.array(tc2_list), np.array(tc1_list))
                # fc_ks_stat, fc_ks_pvalue = stats.ks_2samp(np.array(fc2_list), np.array(fc1_list))

                # Calculates the T-test for the mean of ONE group of scores
                # dspr = np.subtract(np.array(spr2_list), np.array(spr1_list))
                # dtc = np.subtract(np.array(tc2_list), np.array(tc1_list))
                # dfc = np.subtract(np.array(fc2_list), np.array(fc1_list))

                # spr_t_1samp_stat, spr_t_1samp_pvalue = stats.ttest_1samp(dspr, 0.0)
                # tc_t_1samp_stat, tc_t_1samp_pvalue = stats.ttest_1samp(dtc, 0.0)
                # fc_t_1samp_stat, fc_t_1samp_pvalue = stats.ttest_1samp(dfc, 0.0)

                # Calculates the T-test for the means of two independent samples of scores.
                # spr_ttest_ind_stat, spr_ttest_ind_pvalue = stats.ttest_ind(np.array(spr2_list), np.array(spr1_list))
                # tc_ttest_ind_stat, tc_ttest_ind_pvalue = stats.ttest_ind(np.array(tc2_list), np.array(tc1_list))
                # fc_ttest_ind_stat, fc_ttest_ind_pvalue = stats.ttest_ind(np.array(fc2_list), np.array(fc1_list))


                self.plot(mpbs_name, tccorrectedSignalMean1, tccorrectedSignalMean2,
                          pwm_dict, self.output_location, self.output_prefix)

        # Compute the median differences of the all motifs
        mean_diff_prot = list()
        mean_diff_tc = list()
        for mpbs_name in mpbs_name_list:
            mean_diff_prot.append(np.mean(np.array(prot_dict2[mpbs_name])- np.array(prot_dict1[mpbs_name])))
            mean_diff_tc.append(np.mean(np.array(tc_dict2[mpbs_name]) - np.array(tc_dict1[mpbs_name])))

        median_diff_prot = np.median(np.array(mean_diff_prot))
        median_diff_tc = np.median(np.array(mean_diff_tc))
        self.output_mu(median_diff_prot, median_diff_tc)

        mu = [median_diff_prot, median_diff_tc]
        output_file = os.path.join(self.output_location, "{}.txt".format(self.output_prefix))
        f = open(output_file, "a")
        for mpbs_name in mpbs_name_list:
            count = len(prot_dict1[mpbs_name])

            mean_prot1 = sum(prot_dict1[mpbs_name]) / count
            mean_prot2 = sum(prot_dict2[mpbs_name]) / count
            mean_tc1 = sum(tc_dict1[mpbs_name]) / count
            mean_tc2 = sum(tc_dict2[mpbs_name]) / count
            mean_diff_prot = mean_prot2 - mean_prot1
            mean_diff_tc = mean_tc2 - mean_tc1

            #diff_prot = list()
            #for i in range(len(prot_dict2[mpbs_name])):
            #    diff = max(0, prot_dict2[mpbs_name][i]) - max(0, prot_dict1[mpbs_name][i])
            #    diff_prot.append(diff)
            diff_prot = np.array(prot_dict2[mpbs_name]) - np.array(prot_dict1[mpbs_name])
            diff_tc = np.array(tc_dict2[mpbs_name]) - np.array(tc_dict1[mpbs_name])

            X = [diff_prot.tolist(), diff_tc.tolist()]
            stats, pvalue = self.hotellings(np.matrix(X).T, np.matrix(mu))

            res = [mean_prot1, mean_prot2, mean_diff_prot, mean_tc1, mean_tc2, mean_diff_tc, stats, pvalue, count]
            f.write("\t".join([mpbs_name] + [str(i) for i in res]))
            f.write("\n")
        f.close()

    def diff3(self):
        mpbs = GenomicRegionSet("Motif Predicted Binding Sites")
        mpbs.read_bed(self.mpbs_file)

        mpbs_name_list = list(set(mpbs.get_names()))

        genome_data = GenomeData(self.organism)
        fasta = Fastafile(genome_data.get_genome())

        bias_table1 = None
        bias_table2 = None
        if self.bias_table1:
            table_list = self.bias_table1.split(",")
            bias_table1 = BiasTable().load_table(table_file_name_F=table_list[0], table_file_name_R=table_list[1])
        if self.bias_table2:
            table_list = self.bias_table2.split(",")
            bias_table2 = BiasTable().load_table(table_file_name_F=table_list[0], table_file_name_R=table_list[1])

        # Compute the normalization factor based on Housekeeping Gene Normalization Approach
        housekeeping_genes = GenomicRegionSet("housekeeping genes")
        housekeeping_genes.read_bed(self.housekeeping_genes)
        total_tc1 = 0
        total_tc2 = 0
        signal1 = GenomicSignal(self.reads_file1)
        signal2 = GenomicSignal(self.reads_file2)
        for region in housekeeping_genes:
            total_tc1 += signal1.get_tag_count(ref=region.chrom, start=region.initial, end=region.final,
                                              downstream_ext=self.downstream_ext, upstream_ext=self.upstream_ext,
                                              forward_shift=self.forward_shift, reverse_shift=self.reverse_shift)
            total_tc2 += signal2.get_tag_count(ref=region.chrom, start=region.initial, end=region.final,
                                              downstream_ext=self.downstream_ext, upstream_ext=self.upstream_ext,
                                              forward_shift=self.forward_shift, reverse_shift=self.reverse_shift)

        mean_tc = (total_tc1 + total_tc2) / 2.0
        factor1 = mean_tc / total_tc1
        factor2 = mean_tc / total_tc2

        self.output_factor(factor1, factor2)

        # Iterating on MPBSs
        bam1 = Samfile(self.reads_file1, "rb")
        bam2 = Samfile(self.reads_file2, "rb")
        for mpbs_name in mpbs_name_list:
            pwm_dict = dict([("A", [0.0] * self.window_size), ("C", [0.0] * self.window_size),
                             ("G", [0.0] * self.window_size), ("T", [0.0] * self.window_size),
                             ("N", [0.0] * self.window_size)])

            mpbs_regions = mpbs.by_names([mpbs_name])
            spr1_list = list()
            spr2_list = list()
            tc1_list = list()
            tc2_list = list()
            fc1_list = list()
            fc2_list = list()
            spr1 = 0.0
            spr2 = 0.0
            dspr = 0.0
            tc1 = 0
            tc2 = 0
            dtc = 0
            fc1 = 0
            fc2 = 0
            dfc = 0
            corrected_signal1 = []
            corrected_signal2 = []
            dcorrected_signal = []
            tc_corrected_signal1 = []
            tc_corrected_signal2 = []
            dtc_corrected_signal = []
            counter = 0.0
            for region in mpbs_regions:
                length = region.final - region.initial
                mid = (region.final + region.initial) / 2
                p1 = max(mid - self.window_size / 2, 0)
                p2 = mid + self.window_size / 2

                sp1, tc_1, fc_1, corrected_signal_1, tc_corrected_signal_1 = \
                    self.get_stats(bam=bam1, bias_table=bias_table1,
                                   genome_file_name=genome_data.get_genome(),
                                   chrName=region.chrom, start=p1, end=p2, motif_length=length, factor=factor1)

                sp2, tc_2, fc_2, corrected_signal_2, tc_corrected_signal_2 = \
                    self.get_stats(bam=bam2, bias_table=bias_table2,
                                   genome_file_name=genome_data.get_genome(),
                                   chrName=region.chrom, start=p1, end=p2, motif_length=length, factor=factor2)
                spr1 += sp1
                spr2 += sp2
                #dspr += max(0, sp2) - max(0, sp1)
                dspr += sp2 - sp1
                tc1 += tc_1
                tc2 += tc_2
                dtc += tc_2 - tc_1
                fc1 += fc_1
                fc2 += fc_2
                dfc += fc_2 - fc_1

                spr1_list.append(sp1)
                spr2_list.append(sp2)
                tc1_list.append(tc_1)
                fc1_list.append(fc_1)

                tc2_list.append(tc_2)
                fc2_list.append(fc_2)

                if (region.orientation == "-"):
                    corrected_signal_1 = corrected_signal_1[::-1]
                    corrected_signal_2 = corrected_signal_2[::-1]
                    tc_corrected_signal_1 = tc_corrected_signal_1[::-1]
                    tc_corrected_signal_2 = tc_corrected_signal_2[::-1]

                if (len(corrected_signal1) == 0):
                    corrected_signal1 = corrected_signal_1
                    corrected_signal2 = corrected_signal_2
                    dcorrected_signal = corrected_signal_2 - corrected_signal1
                    tc_corrected_signal1 = tc_corrected_signal_1
                    tc_corrected_signal2 = tc_corrected_signal_2
                    dtc_corrected_signal = tc_corrected_signal_2 - tc_corrected_signal_1
                else:
                    corrected_signal1 += corrected_signal_1
                    corrected_signal2 += corrected_signal_2
                    dcorrected_signal += corrected_signal_2 - corrected_signal1
                    tc_corrected_signal1 += tc_corrected_signal_1
                    tc_corrected_signal2 += tc_corrected_signal_2
                    dtc_corrected_signal += tc_corrected_signal_2 - tc_corrected_signal_1
                counter += 1.0

                # Update pwm
                aux_plus = 1
                dna_seq = str(fasta.fetch(region.chrom, p1, p2)).upper()
                if (region.final - region.initial) % 2 == 0:
                    aux_plus = 0
                dna_seq_rev = AuxiliaryFunctions.revcomp(str(fasta.fetch(region.chrom,
                                                                         p1 + aux_plus, p2 + aux_plus)).upper())
                if region.orientation == "+":
                    for i in range(0, len(dna_seq)):
                        pwm_dict[dna_seq[i]][i] += 1
                elif region.orientation == "-":
                    for i in range(0, len(dna_seq_rev)):
                        pwm_dict[dna_seq_rev[i]][i] += 1

            # Updating protection
            if (counter > 0):
                protmean1 = spr1 / counter
                protmean2 = spr2 / counter
                difprotmean = dspr / counter
                tcmean1 = tc1 / counter
                tcmean2 = tc2 / counter
                diftcmean = dtc / counter
                fcmean1 = fc1 / counter
                fcmean2 = fc2 / counter
                diffcmean = dfc / counter
                corrected_signal1_mean = corrected_signal1 / counter
                corrected_signal2_mean = corrected_signal2 / counter
                dcorrected_signal_mean = dcorrected_signal / counter
                tccorrectedSignalMean1 = tc_corrected_signal1 / counter
                tccorrectedSignalMean2 = tc_corrected_signal2 / counter
                dtc_corrected_signal = dtc_corrected_signal / counter

                # Kolmogorov-Smirnov test
                spr_ks_stat, spr_ks_pvalue = stats.ks_2samp(np.array(spr2_list), np.array(spr1_list))
                tc_ks_stat, tc_ks_pvalue = stats.ks_2samp(np.array(tc2_list), np.array(tc1_list))
                fc_ks_stat, fc_ks_pvalue = stats.ks_2samp(np.array(fc2_list), np.array(fc1_list))

                # Calculates the T-test for the mean of ONE group of scores
                dspr = np.subtract(np.array(spr2_list), np.array(spr1_list))
                dtc = np.subtract(np.array(tc2_list), np.array(tc1_list))
                dfc = np.subtract(np.array(fc2_list), np.array(fc1_list))

                spr_t_1samp_stat, spr_t_1samp_pvalue = stats.ttest_1samp(dspr, 0.0)
                tc_t_1samp_stat, tc_t_1samp_pvalue = stats.ttest_1samp(dtc, 0.0)
                fc_t_1samp_stat, fc_t_1samp_pvalue = stats.ttest_1samp(dfc, 0.0)

                # Calculates the T-test for the means of two independent samples of scores.
                spr_ttest_ind_stat, spr_ttest_ind_pvalue = stats.ttest_ind(np.array(spr2_list), np.array(spr1_list))
                tc_ttest_ind_stat, tc_ttest_ind_pvalue = stats.ttest_ind(np.array(tc2_list), np.array(tc1_list))
                fc_ttest_ind_stat, fc_ttest_ind_pvalue = stats.ttest_ind(np.array(fc2_list), np.array(fc1_list))

                res = [protmean1, protmean2, difprotmean,
                       spr_ks_stat, spr_ks_pvalue, spr_t_1samp_stat, spr_t_1samp_pvalue, spr_ttest_ind_stat,
                       spr_ttest_ind_pvalue,
                       tcmean1, tcmean2, diftcmean,
                       tc_ks_stat, tc_ks_pvalue, tc_t_1samp_stat, tc_t_1samp_pvalue, tc_ttest_ind_stat,
                       tc_ttest_ind_pvalue,
                       fcmean1, fcmean2, diffcmean,
                       fc_ks_stat, fc_ks_pvalue, fc_t_1samp_stat, fc_t_1samp_pvalue, fc_ttest_ind_stat,
                       fc_ttest_ind_pvalue,
                       counter]
                output_file = os.path.join(self.output_location, "{}.txt".format(self.output_prefix))
                f = open(output_file, "a")
                f.write("\t".join([mpbs_name] + [str(i) for i in res]))
                f.write("\n")
                f.close()

                self.plot(mpbs_name, tccorrectedSignalMean1, tccorrectedSignalMean2,
                          pwm_dict, self.output_location, self.output_prefix)

    def plot(self, mpbs_name, tccorrectedSignalMean1, tccorrectedSignalMean2,
             pwm_dict, output_location, output_prefix):
        # Output PWM and create logo
        loc = os.path.join(output_location, output_prefix)
        pwm_fname = os.path.join(loc, "{}.pwm".format(mpbs_name))
        pwm_file = open(pwm_fname, "w")
        for e in ["A", "C", "G", "T"]:
            pwm_file.write(" ".join([str(int(f)) for f in pwm_dict[e]]) + "\n")
        pwm_file.close()

        logo_fname = os.path.join(loc, "{}.logo.eps".format(mpbs_name))
        pwm = motifs.read(open(pwm_fname), "pfm")
        pwm.weblogo(logo_fname, format="eps", stack_width="large", stacks_per_line=str(self.window_size),
                    color_scheme="color_classic", unit_name="", show_errorbars=False, logo_title="",
                    show_xaxis=False, xaxis_label="", show_yaxis=False, yaxis_label="",
                    show_fineprint=False, show_ends=False)

        # Output the raw, bias corrected signal and protection score
        output_fname = os.path.join(loc, "{}.txt".format(mpbs_name))
        output_file = open(output_fname, "w")
        output_file.write("tc corrected signal1: \n" + np.array_str(np.array(tccorrectedSignalMean1)) + "\n")
        output_file.write("tc corrected signal2: \n" + np.array_str(np.array(tccorrectedSignalMean2)) + "\n")
        output_file.close()

        start = -(self.window_size / 2)
        end = (self.window_size / 2) - 1
        x = np.linspace(start, end, num=self.window_size)

        fig, ax = plt.subplots()

        cell_names = output_prefix.split("_")
        ax.plot(x, tccorrectedSignalMean1, color='red', label=cell_names[0])
        ax.plot(x, tccorrectedSignalMean2, color='blue', label=cell_names[1])

        ax.xaxis.set_ticks_position('bottom')
        ax.yaxis.set_ticks_position('left')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_position(('outward', 15))
        ax.tick_params(direction='out')
        ax.set_xticks([start, 0, end])
        ax.set_xticklabels([str(start), 0, str(end)])
        min_signal = min(min(tccorrectedSignalMean1), min(tccorrectedSignalMean2))
        max_signal = max(max(tccorrectedSignalMean1), max(tccorrectedSignalMean2))
        ax.set_yticks([min_signal, max_signal])
        ax.set_yticklabels([str(round(min_signal, 2)), str(round(max_signal, 2))], rotation=90)

        ax.set_title(mpbs_name, fontweight='bold')
        ax.set_xlim(start, end)
        ax.set_ylim([min_signal, max_signal])
        ax.legend(loc="upper right", frameon=False)
        ax.set_ylabel("#ATAC-seq reads", rotation=90, fontweight='bold')

        ax.spines['bottom'].set_position(('outward', 40))
        ax.set_xlabel("Coordinates from Motif Center", fontweight='bold')

        figure_name = os.path.join(loc, "{}.line.eps".format(mpbs_name))
        fig.tight_layout()
        fig.savefig(figure_name, format="eps", dpi=300)

        # Creating canvas and printing eps / pdf with merged results
        output_fname = os.path.join(loc, "{}.eps".format(mpbs_name))
        c = pyx.canvas.canvas()
        c.insert(pyx.epsfile.epsfile(0, 0, figure_name, scale=1.0))
        c.insert(pyx.epsfile.epsfile(2.22, 1.55, logo_fname, width=17.5, height=3))
        c.writeEPSfile(output_fname)
        os.remove(figure_name)
        os.remove(logo_fname)
        os.system("epstopdf " + output_fname)

    def output_factor(self, factor1, factor2):
        output_file = os.path.join(self.output_location, "{}_factor.txt".format(self.output_prefix))
        f = open(output_file, "w")
        f.write("Factor1: " + str(factor1) + "\n")
        f.write("Factor2: " + str(factor2) + "\n")
        f.close()

    def output_mu(self, median_diff_prot, median_diff_tc):
        output_file = os.path.join(self.output_location, "{}_mu.txt".format(self.output_prefix))
        f = open(output_file, "w")
        f.write("median_diff_prot: " + str(median_diff_prot) + "\n")
        f.write("median_diff_tc: " + str(median_diff_tc) + "\n")
        f.close()

    def hotellings(self, X, mu):
        '''
        One-sample Hotelling's T2 test.
        :param X:
        :param mu:
        :return:
        '''
        x = X - mu
        n = x.shape[0]
        k = x.shape[1]
        m = x.mean(axis=0)  # mean vector
        S = np.cov(x.T)     # covariance
        t2 = n * m * np.linalg.inv(S) * m.T
        pvalue = stats.chi2.sf(np.sqrt(t2), k)


        return t2.tolist()[0][0], pvalue[0][0]