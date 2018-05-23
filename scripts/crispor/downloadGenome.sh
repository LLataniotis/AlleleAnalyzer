#!/bin/bash
if [ "$1" == "" ] || ["$2" == "" ]; then
    echo usage: downloadGenome genomeCode outputFolder
    echo Downloads pre-indexed and annotated genome from crispor.tefor.org
    echo Example genome codes are: hg19, mm10, sacCer3 etc.
    echo For full list of codes, see: http://crispor.tefor.net/genomes/genomeInfo.all.tab
    exit
else
    wget -r -nH --cut-dirs=2 --no-parent --reject="index.html*,vcf.gz,vcf.gz.tbi"  crispor.tefor.net/genomes/$1/ -P $2/$1
fi