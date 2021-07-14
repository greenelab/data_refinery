###
# illumina.R
#
# Originally written by Stephen Piccolo,
# modified by Rich Jones for Alex's Lemonade Stand Foundation.
#
###

scanNorm <- function(signalExprData, signalProbeSequences, controlExprData=NULL, signalPValueData=NULL, convThreshold=0.5, intervalN=10000, binsize=50, nbins=25, maxIt=100, asUPC=FALSE, numCores=1, verbose=FALSE)
{
  #############################################
  # Check parameters
  #############################################

  if (!is.matrix(signalExprData))
    stop("signalExprData must be a matrix.")
  if (!is.vector(signalProbeSequences))
    stop("signalProbeSequences must be a vector")
  if (nrow(signalExprData) != length(signalProbeSequences))
    stop("The dimensions of signalExprData and signalProbeSequences are incompatible.")
  if (!is.null(signalPValueData))
  {
    if (!is.matrix(signalPValueData))
      stop("signalPValueData must be a matrix.")

    if (all(dim(signalExprData) != dim(signalPValueData)))
      stop("The dimensions of signalExprData and signalPValueData are incompatible.")
  }

  if (!is.null(controlExprData))
  {
    if (!is.matrix(controlExprData))
      stop("controlExprData must be a matrix.")

    if (ncol(signalExprData) != ncol(controlExprData))
      stop("The dimensions of signalExprData and controlExprData are incompatible.")
  }

  #############################################
  # Perform background correction, if necessary
  #############################################

  exprData <- doLog2(signalExprData)

  if (all(signalExprData > 0)) { # No background subtraction has been performed
    if (is.null(controlExprData)) {
      if (!is.null(signalPValueData))
        exprData <- nec(x = doLog2(signalExprData), detection.p = signalPValueData)
    } else {
        status <- c(rep("regular", nrow(signalExprData)), rep("negative", nrow(controlExprData)))

        combinedExprData <- rbind(signalExprData, controlExprData)

        exprData <- nec(x = doLog2(combinedExprData), status = status)
        exprData <- exprData[1:nrow(signalExprData),,drop=FALSE]
    }
  }

  #############################################
  # Normalize
  #############################################

  mx = buildDesignMatrix(signalProbeSequences)

  numSamples <- ncol(exprData)

  if (numSamples > 1 & numCores > 1)
  {
    cl <- makeCluster(numCores, outfile="")
    registerDoParallel(cl)
  }

  if (numSamples == 1)
  {
    normData <- as.matrix(scanNormVector(colnames(exprData)[1], exprData[,1], mx, convThreshold, intervalN, binsize, nbins, maxIt, asUPC, verbose))
  }
  else
  {
    normData <- foreach(i = 1:ncol(exprData), .combine = cbind, .export=c("scanNormVector", "getSampleIndices", "EM_vMix", "mybeta", "assign_bin", "vsig", "vresp", "dn", "vbeta", "sig")) %dopar%
    {
      scanNormVector(colnames(exprData)[i], exprData[,i], mx, convThreshold, intervalN, binsize, nbins, maxIt, asUPC, verbose)
    }
  }

  if (numSamples > 1 & numCores > 1)
    stopCluster(cl)

  rownames(normData) <- rownames(exprData)
  colnames(normData) <- colnames(exprData)

  #normData <- normData[1:nrow(signalExprData),,drop=FALSE]

  return(normData)
}

scanNormVector <- function(description, my, mx, convThreshold, intervalN, binsize, nbins, maxIt, asUPC, verbose)
{
  message(paste("Processing ", description, sep=""))

  # Add some tiny random noise
  set.seed(0)
  noise = rnorm(length(my)) / 10000000
  my = my + noise

  nGroups = floor(length(my) / binsize)
  samplingProbeIndices = getSampleIndices(total=length(my), intervalN=intervalN, verbose=verbose)

  mixResult = EM_vMix(y=my[samplingProbeIndices], X=mx[samplingProbeIndices,], nbins=nbins, convThreshold=convThreshold, maxIt=maxIt, verbose=verbose)

  m1 = mx %*% mixResult$b1
  m2 = mx %*% mixResult$b2

  index = order(m1)
  y_norm = rep(0, length(my))
  for (i in 1:nGroups)
  {
    tmp = index[(binsize * i):min(binsize * i + binsize, length(my))]
    tmpSd = as.vector(sig(y=my[tmp], m=m1[tmp], verbose=verbose))
    y_norm[tmp] = ((my[tmp] - m1[tmp]) / tmpSd)
  }

  bin = assign_bin(y=m1, nbins=nbins, verbose=verbose)
  gam = vresp(y=my, X=mx, bin=bin, p=mixResult$p, b1=mixResult$b1, s1=mixResult$s1, b2=mixResult$b2, s2=mixResult$s2, verbose=verbose)[,2]

  y_norm = round(y_norm, 8)
  gam = round(gam, 8)

  if (asUPC)
  {
    return(gam)
  } else {
    return(y_norm)
  }
}

doLog2 <- function(x)
{
  # This is a semi-crude way of checking whether the values were not previously log-transformed
  if (max(x) > 100)
    x <- log2(x)

  return(x)
}

buildDesignMatrix = function(seqs, verbose=TRUE)
{
  mx = sequenceDesignMatrix(seqs)

  numA = apply(mx[,which(grepl("^A_", colnames(mx)))], 1, sum)
  numC = apply(mx[,which(grepl("^C_", colnames(mx)))], 1, sum)
  numG = apply(mx[,which(grepl("^G_", colnames(mx)))], 1, sum)
  numT = 60 - (numA + numC + numG)

  mx = cbind(numT, mx, as.integer(numA^2), as.integer(numC^2), as.integer(numG^2), as.integer(numT^2))
  mx = apply(mx, 2, as.integer)

  return(mx)
}

getSampleIndices = function(total, intervalN, verbose=TRUE)
{
  interval = floor(total / intervalN)
  if (interval <= 1)
    interval = 1

  seq(1, total, interval)
}

EM_vMix = function(y, X, nbins, convThreshold=.01, maxIt=100, verbose=TRUE)
{
  if (verbose)
    message("Starting EM")

  quan = sort(y)[floor(0.5 * length(y)) - 1]
  gam = cbind(as.integer(y <= quan), as.integer(y > quan))

  p = apply(gam, 2, mean)

  b1 = mybeta(y=y, X=X, gam=gam[,1], verbose=verbose)
  b2 = mybeta(y=y, X=X, gam=gam[,2], verbose=verbose)
  bin = assign_bin(y=y, nbins=nbins, verbose=verbose)
  s1 = vsig(y=y, X=X, b=b1, gam=gam[,1], bin=bin, nbins=nbins, verbose=verbose)
  s2 = vsig(y=y, X=X, b=b2, gam=gam[,1], bin=bin, nbins=nbins, verbose=verbose)

  theta_old=c(p, b1, s1, b2, s2)

  it = 0
  conv = 1000000

  while (conv > convThreshold & it < maxIt)
  {
    # Expectation Step:
    gam = vresp(y=y, X=X, bin=bin, p=p, b1=b1, s1=s1, b2=b2, s2=s2, verbose=verbose)

    #M-Step
    p = apply(gam, 2, mean)
    b1 = vbeta(y=y, X=X, bin=bin, gam=gam[,1], s2=s1, prof=TRUE, verbose=verbose)
    bin = assign_bin(y=(X %*% b1), nbins=nbins, verbose=verbose)
    b2 = vbeta(y=y, X=X, bin=bin, gam=gam[,2], s2=s2, prof=FALSE, verbose=verbose)
    s1 = vsig(y=y, X=X, b=b1, gam=gam[,1], bin=bin, nbins=nbins, verbose=verbose)
    s2 = vsig(y=y, X=X, b=b2, gam=gam[,2], bin=bin, nbins=nbins, verbose=verbose)

    theta = c(p, b1, s1, b2, s2)
    conv = max(abs(theta - theta_old) / theta_old)
    theta_old = theta
    it = it + 1

    if (verbose)
      message("Attempting to converge...iteration ", it, ", c = ", round(conv, 6))
  }

  if (verbose)
  {
    if (it == maxIt)
    {
      message("Reached convergence limit...", it, " iterations. Proportion of background probes: ", round(p[1], 6))
    } else {
      message("Converged in ", it, " iterations. Proportion of background probes: ", round(p[1], 6))
    }
  }

  list(p=p, b1=b1, b2=b2, s1=s1, s2=s2, bin=bin)
}

mybeta = function(y, X, gam, verbose=TRUE)
{
  sqgam = sqrt(gam)
  Xw = sqgam * X
  yw = sqgam * y

  z = t(Xw) %*% Xw
  a = solve(z)

  b = a %*% t(Xw)
  as.numeric(b %*% yw)
}

assign_bin = function(y, nbins, verbose=TRUE)
{
  quans = sort(y)[floor(length(y) * 1:nbins / nbins)]
  bins = sapply(y, function(x) { sum(x>quans) }) + 1

  if (length(table(bins)) != nbins)
  {
    if (verbose)
      message("The values were not separated into enough bins, so a tiny amount of noise will be added to make this possible.")

    set.seed(1)
    noise = rnorm(length(y)) / 10000000
    bins = assign_bin(y + noise, nbins, verbose)
  }

  bins
}

vsig = function(y, X, b, gam, bin, nbins, verbose=TRUE)
{
  s2 = NULL

  for (i in 1:nbins)
  {
    ystar = y[bin==i]
    Xstar = X[bin==i,]
    gamstar = gam[bin==i] + .01
    resid = as.numeric(ystar - Xstar %*% b)

    s2 = c(s2, ((resid * gamstar) %*% resid) / sum(gamstar))
  }

  s2
}

vresp = function(y, X, bin, p, b1, s1, b2, s2, verbose=TRUE)
{
  vars0 = s1[bin]
  L0 = dn(y=y, m=(X %*% b1), s2=vars0, verbose=verbose)
  vars1 = s2[bin]
  L1 = dn(y=y, m=(X %*% b2), s2=vars1, verbose=verbose)

  gam1 = p[1] * L0 / (p[1] * L0 + p[2] * L1)
  gam2 = 1 - gam1
  cbind(gam1, gam2)
}

dn = function(y, m, s2, verbose=TRUE)
{
  1 / (sqrt(2 * pi * s2)) * exp(-1 / (2 * s2) * (y - m)^2)
}

vbeta = function(y, X, bin, gam, s2, prof, verbose=TRUE)
{
  vars = sqrt(s2[bin])
  sqgam = sqrt(gam)
  vars_sqgam = vars * sqgam

  Xw = 1 / vars * sqgam * X
  yw = 1 / vars * sqgam * y

  tXw = t(Xw)
  tXwXw = tXw %*% Xw
  stXwXw = solve(tXwXw)
  stXwXwtXw = stXwXw %*% tXw
  result = stXwXwtXw %*% yw

  result
}

sig = function(y, m, verbose=TRUE)
{
  resid = y - m
  sqrt((resid %*% resid) / length(y))
}

#######################
# The command interface!
#######################

suppressPackageStartupMessages(library("optparse"))

option_list = list(
  make_option(c("-p", "--probeId"), type="character", default="PROBE_ID",
              help="Probe ID", metavar="character"),
  make_option(c("-e", "--expression"), type="character", default=".AVG_Signal",
              help="expression", metavar="character"),
  make_option(c("-d", "--detection"), type="character", default="Detection[ _]Pval",
              help="Detection Pval", metavar="character"),
  make_option(c("-l", "--platform"), type="character", default="illuminaHumanv4",
              help="Platform", metavar="character"),
  make_option(c("-c", "--cores"), type="character", default="1",
              help="Number of cores", metavar="character"),
  make_option(c("-i", "--inputFile"), type="character", default="",
              help="inputFile", metavar="character"),
  make_option(c("-o", "--outputFile"), type="character", default="",
              help="outputFile", metavar="character")
);

opt_parser = OptionParser(option_list=option_list);
opt = parse_args(opt_parser);

probeIDColumn <- opt$probeId
exprColumns <- strsplit(opt$expression, ",")
exprColumns <- unlist(lapply(exprColumns, as.integer))
detectionPValueColumnPattern <- opt$detection
platform <- opt$platform
numCores <- strtoi(opt$cores)
filePath <- opt$inputFile
outFilePath <- opt$outputFile

suppressPackageStartupMessages(library(limma))
suppressPackageStartupMessages(library(oligo))
suppressPackageStartupMessages(library(doParallel))
suppressPackageStartupMessages(library(data.table))
suppressPackageStartupMessages(library(lazyeval))
suppressPackageStartupMessages(library(paste(platform, ".db", sep=""), character.only=TRUE))

# Read the data file
message("Reading data file...")
suppressWarnings(data <- fread(filePath, stringsAsFactors=FALSE, sep="\t", header=TRUE, autostart=10, data.table=FALSE, check.names=FALSE, fill=TRUE, na.strings=c("", "NA"), showProgress=FALSE))

# Check input paramters and parse out data we need
if (probeIDColumn == ""){
  message("Got no probID column...")
  probeIDColumn <- "PROBE_ID"
}

if (!(probeIDColumn %in% colnames(data)))
{
  stop(paste0("No ", probeIDColumn, " column in the data file."))
}

probeIDs <- data[,probeIDColumn]


if (length(exprColumns) == 0)
  stop("No expression columns provided")

exprData <- as.matrix(data[,exprColumns,drop=FALSE])
rownames(exprData) <- probeIDs

# Throw out NA rows
exprData <- na.omit(exprData)

if (detectionPValueColumnPattern == "")
  detectionPValueColumnPattern <- "Detection[ _]Pval"
pValueColumns <- grep(detectionPValueColumnPattern, colnames(data), ignore.case=TRUE)

if (length(pValueColumns) == 0)
  stop(paste0("No columns match this pattern: ", detectionPValueColumnPattern))

if (length(exprColumns) != length(pValueColumns))
  stop(paste0("The number of expression columns [", length(exprColumns), "] did not match the number of p-value columns [", length(pValueColumns), "]."))

pValueData <- as.matrix(data[,pValueColumns,drop=FALSE])
rownames(pValueData) <- probeIDs

# Extract platform-specific data
# See here for more discussion:
# https://github.com/AlexsLemonade/refinebio/pull/212#discussion_r184864928
probeSequenceRef <- lazy_eval(paste0(platform, ".db::", platform, "PROBESEQUENCE"))
probeQualityRef <- lazy_eval(paste0(platform, ".db::", platform, "PROBEQUALITY"))
probeGeneRef <-  lazy_eval(paste0(platform, ".db::", platform, "ENSEMBL"))

# Parse probe sequence info
probeSequences <- as.data.frame(probeSequenceRef[mappedkeys(probeSequenceRef)])
rownames(probeSequences) <- probeSequences[,1]

# Limit to probes that overlap between data and probe sequences
commonProbes <- intersect(rownames(exprData), rownames(probeSequences))
if (length(commonProbes) < 1000)
  stop("Not enough probes overlap between the annotations and the data.")

exprData <- exprData[commonProbes,,drop=FALSE]
pValueData <- pValueData[commonProbes,,drop=FALSE]
probeSequences <- probeSequences[commonProbes,2]

# Do normalization
normData <- scanNorm(exprData, probeSequences, signalPValueData=pValueData, numCores=numCores)

# Parse probe quality info
# (It makes sense to do this after normalization so the SCAN model has more data to work with)
probeQuality <- as.data.frame(probeQualityRef[mappedkeys(probeQualityRef)])
goodProbeIndices <- which(grepl("Good", probeQuality$ProbeQuality))
perfectProbeIndices <- which(grepl("Perfect", probeQuality$ProbeQuality))
probesToKeep <- probeQuality[c(goodProbeIndices, perfectProbeIndices),1]
probesToKeep <- intersect(probesToKeep, rownames(normData))

normData <- normData[probesToKeep,,drop=FALSE]

# Map probes to genes
probeGene <- as.data.frame(probeGeneRef[mappedkeys(probeGeneRef)])
rownames(probeGene) <- probeGene$probe_id
probesToKeep <- sort(intersect(rownames(probeGene), rownames(exprData)))
probeGene <- probeGene[probesToKeep,]

normData <- merge(probeGene, as.data.frame(normData), by=0, sort=FALSE)
normData <- normData[,-c(1,2)]
colnames(normData)[1] <- "GeneID"

# Save to output file
write.table(normData, outFilePath, row.names=FALSE, col.names=TRUE, quote=FALSE, sep="\t")
