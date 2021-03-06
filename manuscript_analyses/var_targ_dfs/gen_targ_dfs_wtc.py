#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gen_targ_dfs.py generates a dataframe that stores annotations for each variant in 
the specified locus and genome that tell us whether the variant generates allele-specific 
sgRNA sites for the Cas variety/varieties specified. Written in Python v 3.6.1.
Kathleen Keough et al 2018.
Usage:
    gen_targ_dfs.py <gens_file> <cas> <pams_dir> <ref_genome_fasta> <out_dir> [--multi] [--mp]

Arguments:
    gens_file           Explicit genotypes file generated by get_chr_tables.sh
    cas                 Types of cas, comma-separated.
    pams_dir            Directory where pam locations in ref_genome are located. 
    ref_genome_fasta          Fasta file for ref_genome 
    out_dir             Directory in which to save the output files.
Options:
    --multi             Run for multiple loci simultaneously.
    --mp                Run as multiprocess using full number of cores. If this is specified, assumes gens_file is actually a directory
                        with files for each chromosome.

available Cas types = cpf1,SpCas9,SpCas9_VRER,SpCas9_EQR,SpCas9_VQR_1,SpCas9_VQR_2,StCas9,StCas9_2,SaCas9,SaCas9_KKH,nmCas9,cjCas9
"""

import pandas as pd
import numpy as np
from docopt import docopt
import os
from pyfaidx import Fasta
import regex
import subprocess

__version__ = '0.0.0'

# 3 and 5 prime cas lists

TP_CAS_LIST = ['SpCas9', 'SpCas9_VRER', 'SpCas9_EQR', 'SpCas9_VQR_1',
               'SpCas9_VQR_2', 'StCas9', 'StCas9_2', 'SaCas9', 'SaCas9_KKH', 'nmCas9', 'cjCas9']

FP_CAS_LIST = ['cpf1']

# "three-prime PAMs, e.g. Cas9, PAM is 3' of the sgRNA sequence"
tpp_for = {}

tpp_for['SpCas9'] = r'[atcg]gg' # SpCas9, SpCas9-HF1, eSpCas1.1
tpp_for['SpCas9_VRER'] = r'[atcg]gcg' # SpCas9 VRER variant
tpp_for['SpCas9_EQR'] = r'[actg]gag' # SpCas9 EQR variant
tpp_for['SpCas9_VQR_1'] = r'[atcg]ga' # SpCas9 VQR variant 1
tpp_for['SpCas9_VQR_2'] = r'[atcg]g[atcg]g' # SpCas9 VQR variant 2
tpp_for['StCas9'] = r'[actg]{2}agaa' # S. thermophilus Cas9
tpp_for['StCas9_2'] = r'[actg]gg[actg]g' # S. thermophilus Cas9 2
tpp_for['SaCas9'] = r'[atcg]{2}g[ag]{2}t' # SaCas9
tpp_for['SaCas9_KKH'] = r'[atcg]{3}[ag]{2}t' # SaCas9 KKH variant
tpp_for['nmCas9'] = r'[atcg]{4}g[ac]tt' # nmCas9
tpp_for['cjCas9'] = r'[actg]{4}aca' # campylobacter jejuni Cas9

# find 3' PAMs on antisense strand (reverse complement)
tpp_rev = {}

tpp_rev['SpCas9_rev'] = r'cc[atcg]' # SpCas9 reverse complement 
tpp_rev['SpCas9_VRER_rev'] = r'cgc[atcg]' # SpCas9 VRER variant reverse complement 
tpp_rev['SpCas9_EQR_rev'] = r'ctc[actg]' # SpCas9 EQR variant reverse complement 
tpp_rev['SpCas9_VQR_1_rev'] = r'[atcg]tc[atcg]' # SpCas9 VQR variant 1 reverse complement 
tpp_rev['SpCas9_VQR_2_rev'] = r'c[atcg]c[atcg]' # SpCas9 VQR variant 2 reverse complement 
tpp_rev['StCas9_rev'] = r'ttct[actg]{2}' # S. thermophilus Cas9 reverse complement
tpp_rev['StCas9_2_rev'] = r'g[atcg]gg[atcg]' # S. thermophilus Cas9 2 reverse complement 
tpp_rev['SaCas9_rev'] = r't[tc]{2}c[atcg]{2}' # SaCas9 reverse complement 
tpp_rev['SaCas9_KKH_rev'] = r'a[tc]{2}[atcg]{3}' # SaCas9 KKH variant reverse complement 
tpp_rev['nmCas9_rev'] = r'aa[tg]c[atcg]{4}' # NmCas9 reverse complement 
tpp_rev['cjCas9_rev'] = r'tgt[actg]{4}' # campylobacter jejuni Cas9

# "five-prime PAMs, e.g. cpf1, PAM is 5' of the sgRNA sequence"
fpp_for = {}

fpp_for['cpf1'] = r'ttt[atcg]' # Cpf1, PAM 5' of guide

# find 5' PAMs on antisense strand (reverse complement)
fpp_rev = {}

fpp_rev['cpf1_rev'] = r'[atcg]aaa' # Cpf1, PAM 5' of guide


def get_range_upstream(pam_pos):
    """
    Get positions 20 bp upstream, i.e. for forward 3' PAMs or reverse 5' PAMs
    :param pam_pos: position of PAM, int.
    :return: sgRNA seed region positions, set of ints.
    """
    sgrna = set(range(pam_pos - 21, pam_pos))
    return sgrna


def get_range_downstream(pam_pos):
    """
    Get positions 20 bp upstream, i.e. for forward 3' PAMs or reverse 5' PAMs
    :param pam_pos: position of PAM, int.
    :return: sgRNA seed region positions, set of ints.
    """
    sgrna = set(range(pam_pos + 1, pam_pos + 21))
    return sgrna


def find_spec_pams(cas,python_string,orient='3prime'):
    # orient specifies whether this is a 3prime PAM (e.g. Cas9, PAM seq 3' of sgRNA)
    # or a 5prime PAM (e.g. cpf1, PAM 5' of sgRNA)

    # get sequence 

    sequence = python_string

    # get PAM sites (the five prime three prime thing will need to be reversed for cpf1)

    def get_pam_fiveprime(pam_regex,sequence):
        starts = set()
        for pam in regex.finditer(pam_regex, sequence,regex.IGNORECASE,overlapped=True):
            starts.add(pam.start()+1) 
        return(set(starts))

    def get_pam_threeprime(pam_regex,sequence):
        starts = set()
        for pam in regex.finditer(pam_regex, sequence,regex.IGNORECASE,overlapped=True):
            starts.add(pam.end()) 
        return(set(starts))

    if orient == '3prime':
        for_starts = get_pam_fiveprime(tpp_for[cas],sequence)
        rev_starts = get_pam_threeprime(tpp_rev[cas+'_rev'],sequence)
    elif orient == '5prime':
        for_starts = get_pam_threeprime(fpp_for[cas],sequence)
        rev_starts = get_pam_fiveprime(fpp_rev[cas+'_rev'],sequence)

    return(for_starts,rev_starts)



def makes_breaks_pam(cas, chrom, pos, ref, alt, ref_genome):

    """
    Determine if cas in question makes or breaks PAM sites.
    :param chrom: chromosome, int.
    :param pos: position, int.
    :param ref: ref genotype, str.
    :param alt: alt genotype, str.
    :param ref_genome: ref_genome fasta file, fasta.
    :return:
    """
    makes_pam = False
    breaks_pam = False

    var = pos

    if '<' in alt:
        return makes_pam, breaks_pam

    # if alt is not a special case (CNV or SV), continue checking the new sequence

    ref_seq = ref_genome[str(chrom)][pos - 11:pos + 10]

    if len(ref) > len(alt):  # handles deletions
        alt_seq = ref_genome[str(chrom)][var - 11:var - 1] + alt + ref_genome[str(chrom)][
                                                             var + len(ref) + len(alt) - 2:var + len(ref) + len(
                                                                 alt) - 2 + 10]
    else:
        alt_seq = ref_genome[str(chrom)][var - 11:var - 1] + alt + ref_genome[str(chrom)][
                                                             var + len(alt) - 1:var + len(alt) - 1 + 10]

    if cas == 'cpf1':
        ref_pams_for, ref_pams_rev = find_spec_pams(cas, ref_seq, orient='5prime')
        alt_pams_for, alt_pams_rev = find_spec_pams(cas, alt_seq, orient='5prime')
    else:
        ref_pams_for, ref_pams_rev = find_spec_pams(cas, ref_seq)
        alt_pams_for, alt_pams_rev = find_spec_pams(cas, alt_seq)

    if len(alt_pams_for) - len(ref_pams_for) > 0 or len(alt_pams_rev) - len(ref_pams_rev) > 0:
        makes_pam = True
    elif len(ref_pams_for) - len(alt_pams_for) > 0 or len(ref_pams_rev) - len(alt_pams_rev) > 0:
        breaks_pam = True

    return makes_pam, breaks_pam

def get_made_broke_pams(df, chrom, ref_genome):

    """
    Apply makes_breaks_pams to a df.
    :param df: gens df generated by get_chr_tables.sh, available on EF github.
    :param chrom: chromosome currently being analyzed.
    :param ref_genome: ref_genome fasta, pyfaidx format.
    :return: dataframe with indicators for whether each variant makes/breaks PAMs, pd df.
    """
    for cas in cas_list:
        makes, breaks = zip(*df.apply(lambda row: makes_breaks_pam(cas, chrom, row['pos'], row['ref'], row['alt'], ref_genome), axis=1))
        df[f'makes_{cas}'] = makes
        df[f'breaks_{cas}'] = breaks
    return df

def annot_variants(args):
    if type(args) == tuple:
        args = args[1]
    out_dir = args['<out_dir>']
    pams_dir = args['<pams_dir>']
    gens = args['<gens_file>']
    ref_genome = Fasta(args['<ref_genome_fasta>'], as_raw=True)

    # load chromosome variants

    # gens = pd.read_hdf(gens, 'all')
    # this is now done in a bash script
    # norm_cmd = f"bcftools norm -r 3:129247482-129254187 -m - {gens}"
    # bcl_norm = subprocess.Popen(norm_cmd,shell=True, stdout=subprocess.PIPE)
    # bcl_query = subprocess.Popen("bcftools query -f '%CHROM\t%POS\t%REF\t%ALT{0}\n'",shell=True,
    #          stdin=bcl_norm.stdout, stdout=subprocess.PIPE)
    # bcl_query.wait()
    # out = StringIO(bcl_query.communicate()[0].decode("utf-8"))
    gens = pd.read_csv(gens, sep='\t', header=None, names=['chrom','pos','ref','alt'])

    chrom = str(gens.chrom.tolist()[0])
    if not chrom.startswith('chr'):
        chrom = 'chr' + chrom
    chr_variants = set(gens['pos'].tolist())

    # save locations of PAM proximal variants to dictionary

    pam_prox_vars = {}

    # get variants within sgRNA region for 3 prime PAMs (20 bp upstream of for pos and vice versa)

    for cas in cas_list:
        if cas in TP_CAS_LIST:
            print(cas)
            cas_prox_vars = []
            pam_dict = {}
            pam_for_pos = np.load(os.path.join(pams_dir, f'{chrom}_{cas}_pam_sites_for.npy')).tolist()
            pam_rev_pos = np.load(os.path.join(pams_dir, f'{chrom}_{cas}_pam_sites_rev.npy')).tolist()
            for pos in pam_for_pos:
                prox_vars = set(get_range_upstream(pos)) & chr_variants
                cas_prox_vars.extend(prox_vars)
                pam_dict[pos] = prox_vars
            for pos in pam_rev_pos:
                prox_vars = set(get_range_downstream(pos)) & chr_variants
                cas_prox_vars.extend(prox_vars)
                pam_dict[pos] = prox_vars
            pam_prox_vars[cas] = cas_prox_vars

    # same for five prime pams

        elif cas in FP_CAS_LIST:
            print(cas)
            cas_prox_vars = []
            pam_dict = {}
            pam_for_pos = np.load(os.path.join(pams_dir, f'{chrom}_{cas}_pam_sites_for.npy')).tolist()
            pam_rev_pos = np.load(os.path.join(pams_dir, f'{chrom}_{cas}_pam_sites_rev.npy')).tolist()
            for pos in pam_for_pos:
                prox_vars = set(get_range_downstream(pos)) & chr_variants
                cas_prox_vars.extend(prox_vars)
                pam_dict[pos] = prox_vars
            for pos in pam_rev_pos:
                prox_vars = set(get_range_upstream(pos)) & chr_variants
                cas_prox_vars.extend(prox_vars)
                pam_dict[pos] = prox_vars
            pam_prox_vars[cas] = cas_prox_vars

    chrdf = get_made_broke_pams(gens, chrom, ref_genome)

    # make_break_df.to_hdf(os.path.join(out_dir, f'chr{chrom}_make_break.hdf5'), 'all', complib='blosc')

    for cas in cas_list:
        # print(cas)
        spec_pam_prox_vars = pam_prox_vars[cas]
        chrdf[f'var_near_{cas}'] = chrdf['pos'].isin(spec_pam_prox_vars)

    cas_cols = []
    for cas in cas_list:
        prelim_cols = [w.replace('cas',cas) for w in ['makes_cas','breaks_cas','var_near_cas']]
        cas_cols.extend(prelim_cols)
    keepcols = ['chrom','pos','ref','alt'] + cas_cols 
    chrdf = chrdf[keepcols]
    chrdf.to_hdf(f'{out_dir}_targ.hdf5', 'all', mode='w', format='table', data_columns=True, complib='blosc')


def main(args):
    out_dir = args['<out_dir>']
    pams_dir = args['<pams_dir>']
    gens = args['<gens_file>']
    ref_genome = Fasta(args['<ref_genome_fasta>'], as_raw=True)

    global cas_list
    cas_list = list(args['<cas>'].split(','))

    if args['--multi']:

        # load various loci info

        gen_loci_file = pd.HDFStore(gens)
        gens_loci = list(gen_loci_file.keys())

        # compute targ for each locus

        out_targ = pd.HDFStore(args['<out_dir>'] + '.h5')

        for locus in gens_loci:
            print(f'running on {locus}')
            gens = pd.read_hdf(gen_loci_file, locus)
            chrom = str(gens['chrom'].tolist()[0])
            if not chrom.startswith('chr'):
                chrom = 'chr' + chrom
            chr_variants = set(gens['pos'].tolist())

            # save locations of PAM proximal variants to dictionary

            pam_prox_vars = {}

            # get variants within sgRNA region for 3 prime PAMs (20 bp upstream of for pos and vice versa)

            for cas in cas_list:
                if cas in TP_CAS_LIST:
                    print(cas)
                    cas_prox_vars = []
                    pam_dict = {}
                    pam_for_pos = np.load(os.path.join(pams_dir, f'{chrom}_{cas}_pam_sites_for.npy')).tolist()
                    pam_rev_pos = np.load(os.path.join(pams_dir, f'{chrom}_{cas}_pam_sites_rev.npy')).tolist()
                    for pos in pam_for_pos:
                        prox_vars = set(get_range_upstream(pos)) & chr_variants
                        cas_prox_vars.extend(prox_vars)
                        pam_dict[pos] = prox_vars
                    for pos in pam_rev_pos:
                        prox_vars = set(get_range_downstream(pos)) & chr_variants
                        cas_prox_vars.extend(prox_vars)
                        pam_dict[pos] = prox_vars
                    pam_prox_vars[cas] = cas_prox_vars

            # same for five prime pams

                elif cas in FP_CAS_LIST:
                    print(cas)
                    cas_prox_vars = []
                    pam_dict = {}
                    pam_for_pos = np.load(os.path.join(pams_dir, f'chr{chrom}_{cas}_pam_sites_for.npy')).tolist()
                    pam_rev_pos = np.load(os.path.join(pams_dir, f'chr{chrom}_{cas}_pam_sites_rev.npy')).tolist()
                    for pos in pam_for_pos:
                        prox_vars = set(get_range_downstream(pos)) & chr_variants
                        cas_prox_vars.extend(prox_vars)
                        pam_dict[pos] = prox_vars
                    for pos in pam_rev_pos:
                        prox_vars = set(get_range_upstream(pos)) & chr_variants
                        cas_prox_vars.extend(prox_vars)
                        pam_dict[pos] = prox_vars
                    pam_prox_vars[cas] = cas_prox_vars

            chrdf = get_made_broke_pams(gens, chrom, ref_genome)

            # make_break_df.to_hdf(os.path.join(out_dir, f'chr{chrom}_make_break.hdf5'), 'all', complib='blosc')

            for cas in cas_list:
                # print(cas)
                spec_pam_prox_vars = pam_prox_vars[cas]
                chrdf[f'var_near_{cas}'] = chrdf['pos'].isin(spec_pam_prox_vars).astype(int)

            out_targ.put(f'{locus}', chrdf, format='table', data_columns=True, complib='blosc')
    elif args['--mp']:
        import multiprocessing as mp
        import glob
        count = min(mp.cpu_count(),22) # only use as many CPUs as available
        pool = mp.Pool(processes=count)
        chroms = list(range(1,23,1))
        multi_args = {}
        for chrom in chroms:
            spec_args = args.copy()
            spec_args['<gens_file>'] = os.path.join(args['<gens_file>'],f'{chrom}_gens.tsv')
            spec_args['<out_dir>'] = args['<out_dir>'] + f'{chrom}_targ.hdf5'
            multi_args[chrom] = spec_args
        values = multi_args.items()
        pool.map(annot_variants, values)
        pool.close()
        pool.join()
    else:
        annot_variants(args)


if __name__ == '__main__':
    arguments = docopt(__doc__, version=__version__)
    main(arguments)
