#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gen_sgRNAs.py generates sgRNAs as part of ExcisionFinder. Written in Python v 3.6.1.
Kathleen Keough et al 2018.

Usage:
    gen_sgRNAs.py [-chv] <bcf> <annots_file> <locus> <pams_dir> <ref_fasta> <out> <cas_types> <guide_length> [<gene_vars>] [--crispor] [<ref_gen>] [--hom] [--bed] [--max_indel=<S>]

Arguments:
    bcf                 BCF/VCF file with genotypes.
    annots_file         Annotated variant for whether each generates an allele-specific sgRNA site.
    locus               Locus of interest in format chrom:start-stop. Put filepath to BED file here if '--bed'.
    pams_dir            Directory where pam locations in the reference genome are located. 
    ref_genome_fasta    Fasta file for reference genome used, e.g. hg38.
    out             Directory in which to save the output files.
    cas_types           Cas types you would like to analyze, comma-separated (e.g. SpCas9,SaCas9).
    guide_length        Guide length, commonly 20 bp, comma-separated if different for different cas types.
    gene_vars           Optional. Gene variants HDF5 file originating from 1000 Genomes Data, formatted
                        in order to add rsID and allele frequency (AF) data to variants. 
Options:
    -h --help
    -c                  Do not take the reverse complement of the guide sequence for '-' stranded guides (when the PAM is on the 5' end).
    -v                  Run in verbose mode (especially useful for debugging, but also for knowing status of script)
    --hom               Use 'homozygous' mode, which is basically finding all CRISPR sites (non-allele-specific) in a more personalized
                        way by taking in individual variants.
    --crispor           Add CRISPOR specificity scores to outputted guides. From Haeussler et al. Genome Biology 2016.
    ref_gen             Directory name of reference genome (complete) which can be downloaded from UCSC (see wiki). This is required
                        if you specify --crispor
    --bed             Design sgRNAs for multiple regions specified in a BED file.
    --max_indel=<S>     Maximum size for INDELS. Must be smaller than guide_length [default: 5].

Available cas types:
cpf1, SpCas9, SpCas9_VRER, SpCas9_EQR, SpCas9_VQR_1, SpCas9_VQR_2, 
StCas9, StCas9_2, SaCas9, SaCas9_KKH, nmCas9, cjCas9

More can be added by modifying CAS_LIST.txt
"""

import pandas as pd
import numpy as np
from docopt import docopt
import os
import cas_object
from pyfaidx import Fasta
from collections import Counter
import regex
import re
from Bio import SeqIO
import subprocess
from io import StringIO
import logging

__version__ = '0.0.1'

REQUIRED_BCFTOOLS_VER = '1.5'


# get rid of annoying false positive Pandas error

pd.options.mode.chained_assignment = None


def find_spec_pams(cas,python_string,orient):
    # orient specifies whether this is a 3prime PAM (e.g. Cas9, PAM seq 3' of sgRNA)
    # or a 5prime PAM (e.g. cpf1, PAM 5' of sgRNA)

    # get sequence 

    sequence = python_string

    # get PAM sites (the five prime three prime thing will need to be reversed for cpf1)

    def get_pam_fiveprime(pam_regex,sequence):
        starts = []
        for pam in regex.finditer(pam_regex, sequence,regex.IGNORECASE,overlapped=True):
            starts.append(pam.start()) 
        return(starts)

    def get_pam_threeprime(pam_regex,sequence):
        starts = []
        for pam in regex.finditer(pam_regex, sequence,regex.IGNORECASE,overlapped=True):
            starts.append(pam.end()) 
        return(starts)

    if orient == "3'":
        for_starts = get_pam_fiveprime(tpp_for[cas][0],sequence)
        rev_starts = get_pam_threeprime(tpp_rev[cas+'_rev'][0],sequence)
    elif orient == "5'":
        for_starts = get_pam_threeprime(fpp_for[cas][0],sequence)
        rev_starts = get_pam_fiveprime(fpp_rev[cas+'_rev'][0],sequence)

    return(for_starts,rev_starts)


def het(genotype):
    # if genotype == '.':
    #     return False
    gen1, gen2 = re.split('/|\|',genotype)
    return gen1 != gen2


def check_bcftools():
    """ 
    Checks bcftools version, and exits the program if the version is incorrect
    """
    version = subprocess.run("bcftools -v | head -1 | cut -d ' ' -f2", shell=True,\
     stdout=subprocess.PIPE).stdout.decode("utf-8").rstrip()
    if float(version) >= float(REQUIRED_BCFTOOLS_VER):
        logging.info(f'bcftools version {version} running')

    else: 
        logging.info(f"Error: bcftools must be >={REQUIRED_BCFTOOLS_VER}. Current version: {version}")
        exit(1)


def get_alt_seq(chrom, pam_start, var_pos, ref, alt, guide_length, ref_genome, strand='positive', var_type='near_pam'):

    if strand == 'positive':
        if var_type == 'near_pam':
            # reference sgRNA
            ref_seq = ref_genome['chr'+str(chrom)][pam_start - guide_length - 1:pam_start - 1]
            # alt sgRNA 
            alt_seq = ref_genome['chr'+str(chrom)][pam_start - guide_length - 1:var_pos - 1].lower() + alt.upper() + ref_genome['chr'+str(chrom)][var_pos + len(alt) - 1:pam_start - 1].lower()

        elif var_type == 'destroys_pam':

            # reference sgRNA
            ref_seq = ref_genome['chr'+str(chrom)][pam_start - guide_length - 1:pam_start - 1]
            # in this case, variant is destroying a PAM, rendering the alternate allele no longer a CRISPR site
            # therefore, for lack of a better solution, return empty alt_seq
            alt_seq = 'G' * guide_length

        elif var_type == 'makes_pam': # this might break with indels

            # reference sgRNA
            ref_seq = 'G' * guide_length

            # in this case, variant is destroying a PAM, rendering the alternate allele no longer a CRISPR site
            # therefore, for lack of a better solution, return empty alt_seq
            alt_seq = ref_genome['chr'+str(chrom)][pam_start - guide_length - 1:pam_start - 1]
        return ref_seq.upper(), alt_seq.upper()

    elif strand == 'negative':
        if var_type == 'near_pam':

            # reference sgRNA
            ref_seq = ref_genome['chr'+str(chrom)][pam_start:pam_start + guide_length]
            # alt sgRNA 
            alt_seq = ref_genome['chr'+str(chrom)][pam_start:var_pos - 1] + alt + ref_genome['chr'+str(chrom)][var_pos + len(alt) - 1:pam_start + guide_length]

        elif var_type == 'destroys_pam':

            # reference sgRNA
            ref_seq = ref_genome['chr'+str(chrom)][pam_start:pam_start + guide_length]

            # in this case, variant is destroying a PAM, rendering the alternate allele no longer a CRISPR site
            # therefore, for lack of a better solution, return empty alt_seq
            alt_seq = 'G' * guide_length

        elif var_type == 'makes_pam': # this might break with indels

            # reference sgRNA
            ref_seq = 'G' * guide_length
            alt_seq = ref_genome['chr'+str(chrom)][pam_start:pam_start + guide_length ]
        return ref_seq.upper(), alt_seq.upper()
    else:

        logging.info ('Must specify strand.')
        exit(1)


def make_rev_comp(s):
    """
    Generates reverse comp sequences from an input sequence.
    """
    return s[::-1].translate(s[::-1].maketrans('ACGT', 'TGCA'))


def get_crispor_scores(out_df, outdir, ref_gen):
    guide_seqs_ref = ['>ref_guide_seqs\n']
    guide_seqs_alt = ['>alt_guide_seqs\n']
    for index, row in out_df.iterrows():
        guide_seqs_ref.append(row['gRNA_ref'] + 'GGGNN\n') # the NN splits things up for CRISPOR
        guide_seqs_alt.append(row['gRNA_alt'] + 'GGGNN\n')
    with open('ref_seqs_nosave.fa', 'w') as f:
        for seq in guide_seqs_ref:
            f.write(seq)
    with open('alt_seqs_nosave.fa', 'w') as f:
        for seq in guide_seqs_alt:
            f.write(seq)
    # get script dir
    scriptsdir = os.path.join(os.path.dirname(__file__), 'crispor')
    run_name = os.path.join(scriptsdir, f'crispor.py --skipAlign --noEffScores -g {ref_gen} {ref_gen}')
    print('Running crispor.')
    #error_out = os.path.join(outdir, 'crispor_error.txt')
    error_out = os.path.join(os.path.dirname(outdir), 'crispor_error.txt')
    command = f'source activate crispor; \
    python2 {run_name} ref_seqs_nosave.fa nosave_ref_scores.tsv &> {error_out};\
    python2 {run_name} alt_seqs_nosave.fa nosave_alt_scores.tsv &> {error_out};\
    source deactivate crispor'
    subprocess.run(command, shell=True)
    print('crispor done')
    # subprocess.run('source deactivate crispor', shell=True)
    # remove seq files
    os.remove('ref_seqs_nosave.fa')
    os.remove('alt_seqs_nosave.fa')
    # grab scores from files outputted from CRISPOR
    score_dir_ref = pd.read_csv('nosave_ref_scores.tsv', sep='\t', header=None, names=['seqId','guideId','targetSeq',
        'mitSpecScore','offtargetCount','targetGenomeGeneLocus'])
    score_dir_alt = pd.read_csv('nosave_alt_scores.tsv', sep='\t', header=None, names=['seqId','guideId','targetSeq',
        'mitSpecScore','offtargetCount','targetGenomeGeneLocus'])
    # remove original score files
    # os.remove('nosave_ref_scores.tsv')
    # os.remove('nosave_alt_scores.tsv')
    # merge score info with original out_df
    merge_df_ref = pd.DataFrame()
    merge_df_ref['scores_ref'] = score_dir_ref['mitSpecScore']
    merge_df_ref['offtargcount_ref'] = score_dir_ref['offtargetCount']
    merge_df_ref['gRNA_ref'] = score_dir_ref['targetSeq'].str[:-3] # get rid of added on PAM site
    merge_df_alt = pd.DataFrame()
    merge_df_alt['scores_alt'] = score_dir_alt['mitSpecScore']
    merge_df_alt['offtargcount_alt'] = score_dir_alt['offtargetCount']
    merge_df_alt['gRNA_alt'] = score_dir_alt['targetSeq'].str[:-3] # get rid of added on PAM site
    # output outdir with its new score columns
    outdf = out_df.merge(merge_df_ref, how='left', on='gRNA_ref')
    outdf = outdf.merge(merge_df_alt, how='left', on='gRNA_alt')
    return(outdf)


def verify_hdf_files(gen_file, annots_file, chrom, start, stop, max_indel):
    """
    Compares the hdf files, and makes sure the hdf files contain 
    variants in the specified range.
    """
    start, stop = int(start), int(stop)
    comp = ['chrom', 'pos', 'ref', 'alt']
    if not gen_file[comp].equals(annots_file[comp]):
        logging.info('ERROR: gen file and targ file variants do not match.')
        exit(1)
    #Check chr
    if not len(Counter(gen_file['chrom']).keys()) == 1:
        logging.info("ERROR: variants map to different chromosomes") # Should exit?
        exit(0)
    # Check vars
    if not all(start < int(i) < stop  for i in gen_file['pos']):
        logging.info('Warning: Not all variants are between the defined ranges')
    if not any(start < int(i) < stop  for i in gen_file['pos']):
        logging.info('ERROR: no variants in defined range.')
    # Iterate through the gens file, remove all rows with indels larger than 'max_indel' (in both the re and alt).
    indel_too_large = [ all(len(i) <= max_indel for i in (row['ref'],row['alt'])) for _, row in gen_file.iterrows() ]
    return gen_file[indel_too_large], annots_file[indel_too_large]


def get_allele_spec_guides(args):
    """ 
    Outputs dataframe with allele-specific guides.
    """

    # load genotypes
    bcf = args['<bcf>']

    # get locus info 
    chrom, start, stop = parse_locus(args['<locus>'])

    # get location of pams directory with stored locations of PAMs in reference genome
    pams_dir = args['<pams_dir>']

    # get guide length
    guide_length = int(args['<guide_length>'])

    # get ref_genome
    ref_genome = args['<ref_fasta>']

    # figure out annotation of VCF/BCF chromosome (i.e. starts with 'chr' or not)
    vcf_chrom = str(subprocess.Popen(f'bcftools view -H {args["<bcf>"]} | cut -f1 | head -1', shell=True, 
        stdout=subprocess.PIPE).communicate()[0])

    # See if chrom contains chr
    if vcf_chrom.startswith('chr'):
        chrstart = True
    else:
        chrstart = False

    chrom = norm_chr(chrom, chrstart)

    # eliminates rows with missing genotypes and gets those where heterozygous
    bcl_v = f'bcftools view -g ^miss -g het -r {chrom}:{start}-{stop} -H {bcf}'
    col_names = ['chrom','pos','rsid','ref','alt','score','random','info','gt','genotype']
    bcl_view = subprocess.Popen(bcl_v, shell=True, stdout=subprocess.PIPE)
    bcl_view.wait()

    gens = pd.read_csv(StringIO(bcl_view.communicate()[0].decode("utf-8")),sep='\t',
    header=None, names=col_names, usecols=['chrom','pos','ref','alt','genotype'])

    # load variant annotations
    var_annots = pd.read_hdf(args['<annots_file>'])

    # remove big indels
    gens, var_annots = verify_hdf_files(gens, var_annots, chrom, start, stop, int(args['--max_indel']))

    # if gens is empty, annots should be too, double check this
    if gens.empty and not var_annots.empty:
        logging.info('Gens and annots not matching up - debug.')
        exit(1)

    # if no variants annotated, no allele-specific guides possilbe
    if gens.empty:
        logging.info('No hetorozygous variants, thus no allele-specific guides for this locus.')
        return None

    # output number of heterozygous variants in locus
    variants = set(gens.pos.tolist())
    logging.info('There are ' + str(len(variants)) + ' heterozygous variants in this locus in this genome.')

    # set up what will become the output dataframe
    grna_df = pd.DataFrame(columns=['chrom','start','stop','ref','alt','variant_position_in_guide','gRNA_ref','gRNA_alt',
    'variant_position','strand','cas_type'])

    # make guides for variants within sgRNA region for 3 prime PAMs (guide_length bp upstream of for pos and vice versa)
    for cas in CAS_LIST:

        # get Cas information
        cas_obj = cas_object.get_cas_enzyme(cas)
        pam_length = len(cas_obj.forwardPam)

        # get positions of PAMs annotated in reference genome
        if not chrom.startswith('chr'):
            chrom = 'chr'+chrom
        pam_for_pos = np.load(os.path.join(pams_dir, f'{chrom}_{cas}_pam_sites_for.npy')).tolist()
        pam_for_pos = list(filter(lambda x: x >= start and x <= stop, pam_for_pos))
        pam_rev_pos = np.load(os.path.join(pams_dir, f'{chrom}_{cas}_pam_sites_rev.npy')).tolist()
        pam_rev_pos = list(filter(lambda x: x >= start and x <= stop, pam_rev_pos))

        logging.info(f'Currently evaluating {cas}.')

        # group variants by in vs. near PAM
        vars_near_pams = var_annots.query(f'var_near_{cas}')
        vars_make_pam = var_annots.query(f'makes_{cas}')
        vars_destroy_pam = var_annots.query(f'breaks_{cas}')

        # design guides for variants near PAMs
        for index, row in vars_near_pams.iterrows():
            var = row['pos']
            proximal_sites_for = list(range(row['pos'], row['pos']+guide_length+1))
            nearby_for_pams = list(set(proximal_sites_for) & set(pam_for_pos))
            for pam_site in nearby_for_pams:

                grna_ref_seq, grna_alt_seq = get_alt_seq(chrom, pam_site, var, row['ref'], row['alt'], guide_length, ref_genome, var_type='near_pam')

                grna_df.loc[ind] = ['chr'+str(chrom), (pam_site - guide_length - 1), (pam_site - 1), row['ref'], row['alt'],
                (pam_site - var - 1 + pam_length), grna_ref_seq, grna_alt_seq, var, '+', cas]
                ind += 1

            proximal_sites_rev = list(range(row['pos']-guide_length,row['pos']))
            nearby_rev_pams = list(set(proximal_sites_rev) & set(pam_rev_pos))
            for pam_site in nearby_rev_pams:

                grna_ref_seq, grna_alt_seq = get_alt_seq(chrom, int(pam_site), int(row['pos']), row['ref'], row['alt'], int(guide_length), ref_genome, 
                    strand='negative', var_type='near_pam')
                if not args['-c']:
                    grna_ref_seq, grna_alt_seq = make_rev_comp(grna_ref_seq), make_rev_comp(grna_alt_seq)

                grna_df.loc[ind] = ['chr'+str(chrom), pam_site, pam_site + guide_length, row['ref'], row['alt'],
                var - pam_site + pam_length - 1, grna_ref_seq, grna_alt_seq, var, '-', cas]
                ind += 1

        # design guides for heterozygous variants that destroy PAMs 
        for index, row in vars_destroy_pam.iterrows():
            var = row['pos']
            ref = row['ref']
            alt = row['alt']

            ref_seq = ref_genome['chr'+str(chrom)][var - 11:var + 10]

            if len(ref) > len(alt):  # handles deletions
                alt_seq = ref_genome['chr'+str(chrom)][var - 11:var - 1] + alt + ref_genome['chr'+str(chrom)][
                                                                     var + len(ref) + len(alt) - 2:var + len(ref) + len(
                                                                         alt) - 2 + 10]
            else:
                alt_seq = ref_genome['chr'+str(chrom)][var - 11:var - 1] + alt + ref_genome['chr'+str(chrom)][
                                                                     var + len(alt) - 1:var + len(alt) - 1 + 10]

            ref_pams_for, ref_pams_rev = find_spec_pams(cas, ref_seq, orient=cas_obj.primeness)
            alt_pams_for, alt_pams_rev = find_spec_pams(cas, alt_seq, orient=cas_obj.primeness)

            lost_pams_for = list(set(ref_pams_for).difference(set(alt_pams_for)))
            lost_pams_rev = list(set(ref_pams_rev).difference(set(alt_pams_rev)))

            for pam in lost_pams_for:
                pam_site = pam + var - 11
                ref_allele = ref
                alt_allele = alt
                grna_ref_seq, grna_alt_seq = get_alt_seq(chrom, pam_site, var, ref_allele, alt_allele, guide_length, ref_genome, 
                    var_type='destroys_pam')

                grna_df.loc[ind] = ['chr'+str(chrom), (pam_site - guide_length), (pam_site), row['ref'], row['alt'],
                (pam_site + pam_length - var), grna_ref_seq, grna_alt_seq, var, '+', cas]
                ind += 1

            for pam in lost_pams_rev:
                pam_site = pam + var - 11
                ref_allele = ref
                alt_allele = alt
                grna_ref_seq, grna_alt_seq = get_alt_seq(chrom, pam_site, var, ref_allele, alt_allele, guide_length, ref_genome, 
                    strand='negative', var_type='destroys_pam')
                if not args['-c']:
                    grna_ref_seq, grna_alt_seq = make_rev_comp(grna_ref_seq), make_rev_comp(grna_alt_seq)

                grna_df.loc[ind] = ['chr'+str(chrom), (pam_site), (pam_site + guide_length), ref_allele, alt_allele,
                (var - pam_site + pam_length - 1), grna_ref_seq, grna_alt_seq, var, '-', cas]
                ind += 1

        # design guides for heterozygous variants that make PAMs 
        for index, row in vars_make_pam.iterrows():
            var = row['pos']
            ref = row['ref']
            alt = row['alt']

            ref_seq = ref_genome['chr'+str(chrom)][var - 11:var + 10]

            if len(ref) > len(alt):  # handles deletions
                alt_seq = ref_genome['chr'+str(chrom)][var - 11:var - 1] + alt + ref_genome['chr'+str(chrom)][
                                                                     var + len(ref) + len(alt) - 2:var + len(ref) + len(
                                                                         alt) - 2 + 10]
            else:
                alt_seq = ref_genome['chr'+str(chrom)][var - 11:var - 1] + alt + ref_genome['chr'+str(chrom)][
                                                                     var + len(alt) - 1:var + len(alt) - 1 + 10]

            ref_pams_for, ref_pams_rev = find_spec_pams(cas, ref_seq, orient=cas_obj.primeness)
            alt_pams_for, alt_pams_rev = find_spec_pams(cas, alt_seq, orient=cas_obj.primeness)

            made_pams_for = list(set(alt_pams_for).difference(set(ref_pams_for)))
            made_pams_rev = list(set(alt_pams_rev).difference(set(ref_pams_rev)))

            for pam in made_pams_for:
                pam_site = var - 11 + pam
                ref_allele = ref
                alt_allele = alt
                grna_ref_seq, grna_alt_seq = get_alt_seq(chrom, pam_site, var, ref_allele, alt_allele, guide_length, ref_genome, 
                    var_type='makes_pam')

                grna_df.loc[ind] = ['chr'+str(chrom), (pam_site - guide_length), (pam_site), row['ref'], row['alt'],
                (pam_site + pam_length - var), grna_ref_seq, grna_alt_seq, var, '+', cas]

            for pam in made_pams_rev:
                pam_site = var - 11 + pam
                ref_allele = ref
                alt_allele = alt
                grna_ref_seq, grna_alt_seq = get_alt_seq(chrom, pam_site, var, ref_allele, alt_allele, guide_length, ref_genome, 
                    strand='negative', var_type='makes_pam')
                if not args['-c']:
                    grna_ref_seq, grna_alt_seq = make_rev_comp(grna_ref_seq), make_rev_comp(grna_alt_seq)

                grna_df.loc[ind] = ['chr'+str(chrom), (pam_site), (pam_site + guide_length), ref_allele, alt_allele,
                (var - pam_site + pam_length - 1), grna_ref_seq, grna_alt_seq, var, '-', cas]
       
    # # filter variants where variant is in N position of PAM
    # # this will need to change per PAM
    # grna_df = grna_df.query('variant_position_in_guide != 2')


    # add specificity scores if specified
    if args['--crispor']:
        out = get_crispor_scores(grna_df, out, args['<ref_gen>'])
    # get rsID and AF info if provided
    if args['<gene_vars>']:
        gene_vars = pd.read_hdf(args['<gene_vars>'])
        if not str(gene_vars['chrom'].tolist()[0]).startswith('chr'):
            gene_vars['chrom'] = list(map(lambda x: 'chr' + str(x), gene_vars['chrom']))
        gene_vars = gene_vars.rename(index=str, columns={"pos": "variant_position"})

        grna_df = grna_df.merge(gene_vars, how='left', on=['chrom','variant_position','ref','alt'])
    return grna_df


def norm_chr(chrom_str, vcf_chrom):
    chrom_str = str(chrom_str)
    if vcf_chrom:
        return chrom_str.replace('chr','')
    elif not vcf_chrom and not chrom_str.startswith('chr'):
        return 'chr' + chrom_str
    else:
        return chrom_str


def parse_locus(locus):
    chrom = locus.split(':')[0]
    start = int(locus.split(':')[1].split('-')[0])
    stop = int(locus.split(':')[1].split('-')[1])
    return chrom, start, stop


def simple_guide_design(args, locus):
    """
    For the case when the individual has no variants in the locus, simply design guides based on reference sequence.
    """
    
    # parse locus
    chrom, start, stop = parse_locus(locus)

    # get location of annotated PAMs in reference genome
    pams_dir = args['<pams_dir>']

    for cas in CAS_LIST:
        #get cas info
        cas_obj = cas_object.get_cas_enzyme(cas)

        guide_length = int(args['<guide_length>'])
        ref_genome = Fasta(args['<ref_fasta>'], as_raw=True)

        # get PAM locations for this variety of Cas
        pam_for_pos = np.load(os.path.join(pams_dir, f'chr{chrom}_{cas}_pam_sites_for.npy')).tolist()
        pam_for_pos = list(filter(lambda x: x >= start and x <= stop, pam_for_pos))
        pam_rev_pos = np.load(os.path.join(pams_dir, f'chr{chrom}_{cas}_pam_sites_rev.npy')).tolist()
        pam_rev_pos = list(filter(lambda x: x >= start and x <= stop, pam_rev_pos))

        # put together data for outputted dataframe

        # strands
        pos_strands = ['positive']*len(pam_for_pos)
        neg_strands = ['negative']*len(pam_rev_pos)

        # start positions
        pos_starts = [ pos-guide_length-1 for pos in pam_for_pos ]
        neg_starts = pam_rev_pos

        # stop positions
        pos_stops = [ pos-1 for pos in pam_for_pos ]
        neg_stops = [ pos+guide_length for pos in pam_rev_pos ]

        # make gRNAs
        guides_out = pd.DataFrame()
        guides_out['pam_pos'] = pam_for_pos + pam_rev_pos
        guides_out['strand'] = pos_strands + neg_strands
        guides_out['start'] = pos_starts + neg_starts
        guides_out['stop'] = pos_stops + neg_stops
        guides_out['ref'] = np.nan
        guides_out['alt'] = np.nan
        guides_out['grna'] = guides_out.apply(lambda row: simple_grnas(row, ref_genome, guide_length, chrom), axis=1)
        guides_out['cas_type'] = cas
        guides_out['chrom'] = chrom
        guides_out['variant_position'] = np.nan
        return guides_out


def simple_grnas(row, ref_genome, guide_length, chrom):
    """
    Design gRNAs in reference genome.
    """
    strand = row['strand']
    if strand == 'positive':
        # reference sgRNA
        ref_seq = ref_genome['chr'+str(chrom)][row['pam_pos'] - guide_length - 1:row['pam_pos'] - 1]
    elif strand == 'negative':
        ref_seq = ref_genome['chr'+str(chrom)][row['pam_pos']:row['pam_pos'] + guide_length]
    return ref_seq


def get_guides(args, locus):
    """
    Outputs dataframe with individual-specific (not allele-specific) guides.
    """

    # parse locus
    chrom, start, stop = parse_locus(locus)
    
    # load variant annotations
    var_annots = pd.read_hdf(args['<annots_file>'], where='pos >= start and pos <= stop')

    # load genotypes
    bcf = args['<bcf>']
    # eliminates rows with missing genotypes
    bcl_v = f'bcftools view -g ^miss -r {chrom}:{start}-{stop} -H {bcf}'
    col_names = ['chrom','pos','rsid','ref','alt','score','random','info','gt','genotype']
    bcl_view = subprocess.Popen(bcl_v, shell=True, stdout=subprocess.PIPE)
    gens = pd.read_csv(StringIO(bcl_view.communicate()[0].decode("utf-8")),sep='\t',
    header=None, names=col_names, usecols=['chrom','pos','ref','alt','genotype'])

    # remove big indels
    gens, var_annots = verify_hdf_files(gens, var_annots, chrom, start, stop, max_indel)

    # if gens is empty, annots should be too, double check this
    if gens.empty and not var_annots.empty:
        logging.info('Gens and annots not matching up - debug.')
        exit(1)

    # if no variants annotated, proceed to simplest design case
    if gens.empty:
        out = simple_guide_design(args, locus)
        return out

    # determine which variants are het and which aren't
    gens['het'] = gens['genotype'].apply(het)
    het_gens = gens.query('het').copy()
    hom_gens = gens.query('not het').copy()
    het_variants = set(het_gens.pos.tolist())
    hom_variants = set(hom_gens.pos.tolist())
    print('There are ' + str(len(het_variants)) + ' heterozygous variants and \
        ' + str(len(hom_variants)) + ' homozygous variants in this locus in this genome.')

    # merge annots and genotypes
    var_annots = var_annots.merge(gens)

    # initialize dictionary to save locations of PAM proximal variants
    pam_prox_vars = {}

    # initialize lists that will eventually become the output dataframe
    starts = []
    stops = []
    refs = []
    alts = []
    grnas = []
    variant_pos_in_guides = [] # keep this for annotation homozygous only
    cas_types = []
    chroms = []
    variants_positions = []
    strands = []
    pam_pos = []

    # get some relevant variables
    pams_dir = args['<pams_dir>']
    guide_length = int(args['<guide_length>'])
    ref_genome = Fasta(args['<ref_fasta>'], as_raw=True)

    # get sgRNAs for each Cas variety
    for cas in CAS_LIST:
        # load Cas data
        cas_obj = cas_object.get_cas_enzyme(cas)
        guide_length = int(args['<guide_length>'])

        # get annotated PAMs on + strand in reference genome
        pam_for_pos = np.load(os.path.join(pams_dir, f'chr{chrom}_{cas}_pam_sites_for.npy')).tolist()
        pam_for_pos = list(filter(lambda x: x >= start and x <= stop, pam_for_pos)) 

        # get annotated PAMs on - strand in reference genome
        pam_rev_pos = np.load(os.path.join(pams_dir, f'chr{chrom}_{cas}_pam_sites_rev.npy')).tolist()
        pam_rev_pos = list(filter(lambda x: x >= start and x <= stop, pam_rev_pos))
        logging.info(f'Currently evaluating {cas}.')

        # get length of PAM
        pam_length = len(cas_obj.forwardPam)

        # get variants that are near a reference-annotated PAM site in the reference genome
        # this is pre-computed based on variant annotation from preprocessing
        cas_prox_vars = []
        vars_near_pams = var_annots.query(f'var_near_{cas}')
        het_vars_near_pams = list(set(vars_near_pams.pos).intersection(set(het_variants)))
        hom_vars_near_pams = list(set(vars_near_pams.pos).intersection(set(hom_variants)))

        # identify variants that occur in a PAM site
        vars_make_pam = var_annots.query(f'makes_{cas}')
        vars_destroy_pam = var_annots.query(f'breaks_{cas}')

        # check each possible existing gRNA for instances that break it or change the sgRNA sequence
        for pos in pam_for_pos:
            # disqualify sgRNAs with het variants in sgRNA seed region
            if any(het_variant in range(pos-guide_length-1,pos) for het_variant in het_vars_near_pams):
                continue
            # disqualify sgRNAs where variant destroys PAM site (het or hom doesn't matter)
            elif any(variant in range(pos, pos+pam_length+1) for variant in vars_destroy_pam):
                continue
            # amend any sgRNAs with homozygous variants in the sgRNA seed region
            elif any(variant in range(pos-guide_length-1,pos) for variant in hom_vars_near_pams):
                vars_in_sgRNA = list(set(list(range(pos-guide_length-1,pos))).intersection(set(hom_vars_near_pams)))
                pam_site = pos
                if len(vars_in_sgRNA) == 1: # amending for >1 variants in seed region not yet supported
                    var = vars_in_sgRNA[0]
                    ref_allele = hom_gens.query('pos == @var')['ref'].item()
                    alt_allele = hom_gens.query('pos == @var')['alt'].item()
                    grna_ref_seq, grna_alt_seq = get_alt_seq(chrom, pam_site, var, ref_allele, 
                    alt_allele, guide_length, ref_genome, var_type='near_pam')
                    starts.append(pos - guide_length - 1)
                    stops.append(pos - 1)
                    refs.append(ref_allele)
                    alts.append(alt_allele)
                    grnas.append(grna_alt_seq)
                    variant_pos_in_guides.append(pam_site - var - 1 + pam_length)
                    strands.append('+')
                    pam_pos.append(pos)
                    chroms.append(chrom)
                    cas_types.append(cas)
                    variants_positions.append(var)
                else:
                    logging.info(f'Multiple variants in guide for PAM @ {pos}, not equipped for this yet. Skipping.')
                    continue
            else:
                # assume sgRNA isn't disrupted by anything
                starts.append(pos - guide_length - 1)
                stops.append(pos - 1)
                refs.append(np.nan)
                alts.append(np.nan)
                grnas.append(ref_genome['chr'+str(chrom)][pos - guide_length - 1:pos - 1])
                variant_pos_in_guides.append(np.nan)
                strands.append('+')
                pam_pos.append(pos)
                chroms.append(chrom)
                cas_types.append(cas)
                variants_positions.append(np.nan)

        # add PAMs made by homozygous variants in forward and reverse direction
        for index, row in var_annots.query(f'(makes_{cas}) and (not het)').iterrows():
            var = row['pos']
            ref = row['ref']
            alt = row['alt']

            ref_seq = ref_genome['chr'+str(chrom)][var - 11:var + 10]

            if len(ref) > len(alt):  # handles deletions
                alt_seq = ref_genome['chr'+str(chrom)][var - 11:var - 1] + alt + ref_genome['chr'+str(chrom)][
                                                                     var + len(ref) + len(alt) - 2:var + len(ref) + 
                                                                     len(alt) - 2 + 10]
            else:
                alt_seq = ref_genome['chr'+str(chrom)][var - 11:var - 1] + alt + ref_genome['chr'+str(chrom)][
                                                                     var + len(alt) - 1:var + len(alt) - 1 + 10]

            ref_pams_for, ref_pams_rev = find_spec_pams(cas, ref_seq, orient=cas_obj.primeness)
            alt_pams_for, alt_pams_rev = find_spec_pams(cas, alt_seq, orient=cas_obj.primeness)

            made_pams_for = list(set(alt_pams_for).difference(set(ref_pams_for)))
            made_pams_rev = list(set(alt_pams_rev).difference(set(ref_pams_rev)))

            for pam_start in made_pams_for:
                pam_site = var - 11 + pam
                ref_allele = ref
                alt_allele = alt
                grna_ref_seq, grna_alt_seq = get_alt_seq(chrom, pam_site, var, ref_allele, alt_allele, guide_length, ref_genome, 
                    var_type='makes_pam')
                starts.append(pam_site - guide_length)
                stops.append(pam_site)
                refs.append(ref_allele)
                alts.append(alt_allele)
                grnas.append(grna_ref_seq)
                variant_pos_in_guides.append(pam_site + pam_length - var)
                cas_types.append(cas)
                chroms.append(chrom)
                variants_positions.append(var)
                strands.append('+')
                pam_pos.append(pam_site)
            for pam_start in made_pams_rev:
                pam_site = var - 11 + pam
                ref_allele = ref
                alt_allele = alt
                grna_ref_seq, grna_alt_seq = get_alt_seq(chrom, pam_site, var, ref_allele, alt_allele, guide_length, ref_genome, 
                    strand='negative', var_type='makes_pam')
                if not args['-c']:
                    grna_ref_seq, grna_alt_seq = make_rev_comp(grna_ref_seq), make_rev_comp(grna_alt_seq)
                start = pam_site
                starts.append(start)
                stop = pam_site + guide_length
                stops.append(stop)
                refs.append(ref_allele)
                alts.append(alt_allele)
                grnas.append(grna_ref_seq)
                var_pos = var - pam_site + pam_length
                variant_pos_in_guides.append(var_pos)
                cas_types.append(cas)
                chroms.append(chrom)
                variants_positions.append(var)
                strands.append('-')
                pam_pos.append(pam_site)
        # evaluate PAMs on negative strand (reverse direction)
        for pos in pam_rev_pos:
            # disqualify sgRNAs with het variants in sgRNA seed region
            if any(het_variant in range(pos+1,pos+22) for het_variant in het_vars_near_pams):
                continue
            # disqualify sgRNAs where variant destroys PAM site (het or hom doesn't matter)
            elif any(variant in range(pos - pam_length, pos+1) for variant in vars_destroy_pam):
                continue
            # amend any sgRNAs with homozygous variants in the sgRNA seed region
            elif any(variant in range(pos+1,pos+22) for variant in hom_vars_near_pams):
                vars_in_sgRNA = list(set(list(range(pos+1,pos+22))).intersection(set(hom_vars_near_pams)))
                pam_site = pos
                if len(vars_in_sgRNA) == 1:
                    var = vars_in_sgRNA[0]
                    # ref_allele = hom_gens.query('pos == @pos')['ref'].item()
                    alt_allele = hom_gens.query('pos == @var')['alt'].item()
                    ref_allele = hom_gens.query('pos == @var')['ref'].item()
                    grna_ref_seq, grna_alt_seq = get_alt_seq(chrom, pam_site, var, ref_allele, 
                    alt_allele, guide_length, ref_genome, var_type='near_pam', strand='negative')
                    starts.append(pam_site)
                    stops.append(pam_site + guide_length)
                    refs.append(ref_allele)
                    alts.append(alt_allele)
                    grnas.append(grna_alt_seq)
                    variant_pos_in_guides.append(var - pam_site + pam_length - 1)
                    strands.append('-')
                    pam_pos.append(pos)
                    chroms.append(chrom)
                    cas_types.append(cas)
                    variants_positions.append(var)
                else:
                    logging.info(f'Multiple variants in guide for PAM @ {pos}, not equipped for this yet. Skipping.')
                    continue
            else:
                # assume sgRNA isn't disrupted by anything
                starts.append(pos)
                stops.append(pos + guide_length)
                refs.append(np.nan)
                alts.append(np.nan)
                grnas.append(ref_genome['chr'+str(chrom)][pos:pos + guide_length])
                variant_pos_in_guides.append(np.nan)
                strands.append('-')
                pam_pos.append(pos)
                chroms.append(chrom)
                cas_types.append(cas)
                variants_positions.append(np.nan)

    # get output DF
    out = pd.DataFrame({'chrom':chroms,'start':starts, 'stop':stops, 'ref':refs, 'alt':alts,
        'variant_position_in_guide':variant_pos_in_guides, 'gRNAs':grnas, 'variant_position':variants_positions,
        'strand': strands, 'cas_type':cas_types})

    # add specificity scores if specified
    if args['--crispor']:
        out = get_crispor_scores(out, args['<out>'], args['<ref_gen>'])

    # get rsID and AF info if provided
    if args['<gene_vars>']:
        gene_vars = pd.read_hdf(args['<gene_vars>'])
        if not str(gene_vars['chrom'].tolist()[0]).startswith('chr'):
            gene_vars['chrom'] = list(map(lambda x: 'chr' + str(x), gene_vars['chrom']))
        gene_vars['variant_position'] = gene_vars['pos']
        out = out.merge(gene_vars, how='left', on=['chrom','variant_position','ref','alt'])
    return out


def multilocus_guides(args):
    # if the user initiated the analysis correctly, load the regions to be analyzed
    regions = pd.read_csv(args['<locus>'], sep='\t', header=0,
        names=['chrom','start','stop','name'])

    # figure out annotation of VCF/BCF chromosome (i.e. starts with 'chr' or not)
    vcf_chrom = str(subprocess.Popen(f'bcftools view -H {args["<bcf>"]} | cut -f1 | head -1', shell=True, 
        stdout=subprocess.PIPE).communicate()[0])

    # See if chrom contains chr
    if vcf_chrom.startswith('chr'):
        chrstart = True
    else:
        chrstart = False

    # correct the notation in the inputted file to match the VCF/BCF chromosome notation
    regions['chrom'] = [ norm_chr(chrom, chrstart) for chrom in regions['chrom'].tolist() ]

    # set this up to catch sgRNA dataframe outputs
    out_list = []

    # initiates multi-locus personalized guide design
    if args['--hom']:
        logging.info('Finding personalized (non-allele-specific) guides.')
        for index, row in regions.iterrows():
            chrom = row['chrom']
            start = row['start']
            stop = row['stop']
            guides_df = get_guides(args, f'{chrom}:{start}-{stop}')
            guides_df['locus'] = row['name']
            out_list.append(guides_df)
    # initiates design of allele-specific guides for multi-locus process
    else:
        logging.info('Finding allele-specific guides.')
        for index, row in regions.iterrows():
            chrom = row['chrom']
            start = row['start']
            stop = row['stop']
            guides_df = get_allele_spec_guides(args, spec_locus=f'{chrom}:{start}-{stop}')
            guides_df['locus'] = row['name']
            out_list.append(guides_df)
    # assembles full output dataframe for all loci evaluated
    out = pd.concat(list(filter(None,out_list)))

    return out


def main(args):
    
    # make sure user has a supported version of bcftools available
    check_bcftools()

    # assemble list of Cas enzymes that will be evaluated
    global CAS_LIST
    CAS_LIST = args['<cas_types>'].split(',')

    # print args (for debugging)
    logging.info(args)

    # determine whether running as multi-locus
    if args['--bed']:
        logging.info('Running as multi-locus, assumes BED file given.')
        if not args['<locus>'].endswith('.bed'):
            logging.info('Error: Must use BED file in place of locus for --bed run. Exiting.')
            exit(1)
        else:
            out = multilocus_guides(args)
    # initiates personalized guide design for single locus
    elif args['--hom']:
        logging.info('Finding non-allele-specific guides.')
        out = get_guides(args)
    # initiates allele-specific, personalized guide design for single locus
    else:
        logging.info('Finding allele-specific guides.')
        out = get_allele_spec_guides(args)
    
    # assign unique identifier to each sgRNA
    out['guide_id'] = 'guide' + out.index.astype(str)
    
    # add variant descriptors from 1KGP to assembled guides (optional)
    if args['<gene_vars>']:
        for i, row in out.iterrows():
            if pd.isnull(row['rsID']):
                out.ix[i,'rsID'] = ':'.join([row['chrom'],str(row['variant_position']),row['ref'], row['alt']])
                out.ix[i,'AF'] = 0

    # saves output
    out.to_csv(args['<out>'] + '_guides.tsv', sep='\t', index=False)
    logging.info('Done.')


if __name__ == '__main__':
    arguments = docopt(__doc__, version=__version__)
    if arguments['-v']:
        logging.basicConfig(level=logging.INFO, format='[%(asctime)s %(name)s:%(levelname)s ]%(message)s')
    else:
        logging.basicConfig(level=logging.ERROR, format='[%(asctime)s %(name)s:%(levelname)s ]%(message)s')
    main(arguments)
