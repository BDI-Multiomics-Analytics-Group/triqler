#!/usr/bin/python

from __future__ import print_function

import itertools

import numpy as np
from scipy.stats import f_oneway, gamma
from scipy.optimize import curve_fit

from . import parsers
from . import convolution_dp
from . import hyperparameters

import sys

def getPosteriors(quantRowsOrig, params, returnDistributions = True):
  
  #print(params)
  quantRows, quantMatrix = parsers.getQuantMatrix(quantRowsOrig)
  
  pProteinQuantsList, bayesQuantRow = getPosteriorProteinRatios(quantMatrix, quantRows, params)
  pProteinGroupQuants = getPosteriorProteinGroupRatios(pProteinQuantsList, bayesQuantRow, params)
  pProteinGroupDiffs, muGroupDiffs = getProteinGroupsDiffPosteriors(pProteinGroupQuants, params)
  
  probsBelowFoldChange = getProbBelowFoldChangeDict(pProteinGroupDiffs, params)
  if returnDistributions:
    #print("returnDistributions is TRUE!")
    #sys.stdout.flush()
    return bayesQuantRow, muGroupDiffs, probsBelowFoldChange, pProteinQuantsList, pProteinGroupQuants, pProteinGroupDiffs
  else:
    #print("returnDistributions is FALSE!")
    #sys.stdout.flush()
    return bayesQuantRow, muGroupDiffs, probsBelowFoldChange

def getPosteriorProteinRatios(quantMatrix, quantRows, params, maxIterations = 50, bayesQuantRow = None):
  #print(len(quantMatrix))
  #print(params.keys())
  #print("")
  numSamples = len(quantMatrix[0])
  #print(numSamples)
  bayesQuantRow = np.array([1.0]*numSamples) #<--- uniform prior????
  
  for iteration in range(maxIterations):  
    prevBayesQuantRow = np.copy(bayesQuantRow)
    pProteinQuantsList, bayesQuantRow = getPosteriorProteinRatio(quantMatrix, quantRows, bayesQuantRow, params)
    #print(len(pProteinQuantsList[8]))    
    #print(len(pProteinQuantsList[0]))
    
    #print(bayesQuantRow)
    bayesQuantRow = parsers.geoNormalize(bayesQuantRow)
    
    diffInIteration = np.log10(prevBayesQuantRow) - np.log10(bayesQuantRow)
    if np.max(diffInIteration*diffInIteration) < 1e-4:
      #print("Converged after iteration", iteration+1)
      break
  
  return pProteinQuantsList, bayesQuantRow

def getPosteriorProteinRatio(quantMatrix, quantRows, geoAvgQuantRow, params):
  numSamples = len(quantMatrix[0])

  if params["knownGroups"] == True:
    
      logGeoAvgsGroups = []
      for row in quantMatrix:
          logGeoAvgs_i = []
          for i in range(len(params["groupLabels"])):
              logGeoAvgs_i.append(np.log10(parsers.geomAvg(row[params["groups"][i]])))
          logGeoAvgsGroups.append(logGeoAvgs_i)
    
      featDiffsGroups = []
      for row in range(len(quantMatrix)):
          featDiffs_i = []
          for i in range(len(params["groupLabels"])):
              featDiffs_i.append(np.log10(quantMatrix[row][params["groups"][i]]) - logGeoAvgsGroups[row][i])
          featDiffsGroups.append(np.concatenate(featDiffs_i, axis = 0))#.tolist())
      featDiffsGroups = np.array(featDiffsGroups)  

      pMissingGeomAvgGroups = [[] for i in range(len(params["groupLabels"]))]  
      for i in range(len(params["groupLabels"])):
          pMissingGeomAvgGroups[i] = pMissing(np.array(logGeoAvgsGroups)[:,i], 
                               params["muDetect"+params["groupLabels"][i]], 
                               params["sigmaDetect"+params["groupLabels"][i]])
          pMissingGeomAvgGroups = np.array(pMissingGeomAvgGroups).T.tolist()

  logGeoAvgs = np.log10([parsers.geomAvg(row) for row in quantMatrix])
  featDiffs = np.log10(quantMatrix) - logGeoAvgs[:,np.newaxis] 
  #featDiffs = featDiffsGroups #<---------------------------------------- NOTE THIS!!!!
  pMissingGeomAvg = pMissing(logGeoAvgs, params["muDetect"], params["sigmaDetect"]) # Pr(f_grn = NaN | t_grn = 1)

  pQuantIncorrectId = hyperparameters.funcHypsec(featDiffs, params["muFeatureDiff"], params["sigmaFeatureDiff"]) # Pr(f_grn = x | t_grn = 1)
  
  if params["knownGroups"] == True:  
      pQuantIncorrectIdGroups = [[] for i in range(len(params["groupLabels"]))]
      for i in range(len(params["groupLabels"])):
          pQuantIncorrectIdGroups[i] = hyperparameters.funcHypsec(featDiffsGroups[:, params["groups"][i]], 
                                                              params["muFeatureDiff"+params["groupLabels"][i]], 
                                                              params["sigmaFeatureDiff"+params["groupLabels"][i]])
      pQuantIncorrectIdGroups = np.concatenate(pQuantIncorrectIdGroups, axis = 1)
  
      xImpsAllGroups = [[] for i in range(len(params["groupLabels"]))]
      for i in range(len(params["groupLabels"])):
          xImpsAllGroups[i] = imputeValues(np.array(quantMatrix)[:, params["groups"][i]],
                        geoAvgQuantRow[params["groups"][i]], params["proteinQuantCandidates"])
      xImpsAllGroups = np.concatenate(xImpsAllGroups, axis = 1)

  xImpsAll = imputeValues(quantMatrix, geoAvgQuantRow, params['proteinQuantCandidates'])

  impDiffs = xImpsAll - np.log10(np.array(quantMatrix))[:,:,np.newaxis]
  
  if params["knownGroups"] == True:
      impDiffsGroups = xImpsAllGroups - np.log10(np.array(quantMatrix))[:,:,np.newaxis]
  
  pDiffs = hyperparameters.funcHypsec(impDiffs, params["muFeatureDiff"], params["sigmaFeatureDiff"]) # Pr(f_grn = x | m_grn = 0, t_grn = 0)
  
  if params["knownGroups"] == True:
      pDiffsGroups = [[] for i in range(len(params["groupLabels"]))]
      for i in range(len(params["groupLabels"])):
          pDiffsGroups[i] = hyperparameters.funcHypsec(impDiffs[:, params["groups"][i],:],
                      params["muFeatureDiff" + params["groupLabels"][i]],
                      params["sigmaFeatureDiff" + params["groupLabels"][i]])
      pDiffsGroups = np.concatenate(pDiffsGroups, axis = 1)

  pProteinQuantsList, bayesQuantRow = list(), list()
  for j in range(numSamples):
    pProteinQuant = params['proteinPrior'].copy() # log likelihood <--------------------- not multiple priors
    """
    #print(pProteinQuant)
    cnt = 0
    for priorGroup, sampleInPrior in enumerate(params["groups"]):
        if j in sampleInPrior:
            if cnt > 0:
                raise ("ERROR with multiple prior assignement <----------------------------------------")
            #print(params["proteinPriorGroups"])
            pProteinQuant = params[params["proteinPriorGroups"][priorGroup]].copy()
            #if np.isnan(pProteinQuant).sum() > 0:
                #print("NaN count: " + str(np.isnan(pProteinQuant).sum()))
                #print(pProteinQuant)
                #raise ("NaN encountered in groupPrior")
            cnt += 1  
            #################################################################################
            # 2019-04-30 START TO CHANGE pMissingGroup, featDiffGroup xImpAllGroup etc etc. #
            # I think params["muDetect" + params["groupLabels"][i]] exists
            ##################################################################################
    """
    '''
    for i, row in enumerate(quantMatrix):
      for priorGroup, sampleInPrior in enumerate(params["groups"]):
          if j in sampleInPrior: #Will be one for each sample... [s01r01, s01r02,... s02r01, ... s10r05]
              """
              NOTE THIS CODE WILL MAKE IT UPDATE likelihood original times the number of groups,
              
              """
              linkPEP = quantRows[i].linkPEP[j]
              identPEP = quantRows[i].identificationPEP[j]
              if identPEP < 1.0:
                pMissings = pMissing(xImpsAll[i,j,:],
                                     params["muDetect"+params["groupLabels"][priorGroup]],
                                     params["sigmaDetect"+params["groupLabels"][priorGroup]]) # Pr(f_grn = NaN | m_grn = 1, t_grn = 0)
                #pMissings_pseudo = pMissing(xImpsAllGroups[i,j,:], params["muDetect"], params["sigmaDetect"])
                #print(xImpsAllGroups[i,j,:])
                if np.isnan(row[j]): 
                  if np.isnan(pMissingGeomAvgGroups[i][priorGroup]): # IF THE GROUP MEAN IS NAN WE SET IT TO ZERO (parameter).
                      pMissingGeomAvgGroups[i][priorGroup] = 0
                  #print(pMissingGeomAvgGroups[i][priorGroup])
                  likelihood = pMissings * (1.0 - identPEP) * (1.0 - linkPEP) + pMissingGeomAvgGroups[i][priorGroup] * (identPEP * (1.0 - linkPEP) + linkPEP)
                else: #TRY TO UNDERSTAND THE RELATIONSHIP BETWEEN THIS AND HYPSEC DISTRIBUTION
                  if np.isnan(pMissingGeomAvgGroups[i][priorGroup]):
                      pMissingGeomAvgGroups[i][priorGroup] = 0 
                  likelihood = (1.0 - pMissings) * pDiffs[i,j,:] * (1.0 - identPEP) * (1.0 - linkPEP) + (1.0 - pMissingGeomAvgGroups[i][priorGroup]) * (pQuantIncorrectIdGroups[i][j] * identPEP * (1.0 - linkPEP) + linkPEP)
                if np.min(likelihood) == 0.0:
                  likelihood += np.nextafter(0,1)
        #likelihood = np.nan_to_num(likelihood)
                if np.isnan(likelihood).sum() > 0:
                    print("NaN count: " + str(np.isnan(likelihood).sum()))
                    print(likelihood)
                    raise ("NaN encountered in likelihood computations")
                pProteinQuant += np.log(likelihood)
        #pProteinQuant = np.nan_to_num(pProteinQuant) # fix NaN issue in protein quants
    pProteinQuant -= np.max(pProteinQuant)
    #print(pProteinQuant)
    pProteinQuant = np.exp(pProteinQuant) / np.sum(np.exp(pProteinQuant))
    pProteinQuantsList.append(pProteinQuant)
    
    #print(len(params["proteinQuantCandidates"]))
    eValue, confRegion = getPosteriorParams(params['proteinQuantCandidates'], pProteinQuant)
    #print(params['proteinQuantCandidates'])
    bayesQuantRow.append(eValue)
    '''
    ############################
    # FOR NON GROUPED ##########
    ############################
    
    for i, row in enumerate(quantMatrix):
      
      linkPEP = quantRows[i].linkPEP[j]
      identPEP = quantRows[i].identificationPEP[j]
      if identPEP < 1.0:
        pMissings = pMissing(xImpsAll[i,j,:], params["muDetect"], params["sigmaDetect"]) # Pr(f_grn = NaN | m_grn = 1, t_grn = 0)
        
        if np.isnan(row[j]):
          likelihood = pMissings * (1.0 - identPEP) * (1.0 - linkPEP) + pMissingGeomAvg[i] * (identPEP * (1.0 - linkPEP) + linkPEP)
        else: #TRY TO UNDERSTAND THE RELATIONSHIP BETWEEN THIS AND HYPSEC DISTRIBUTION
          likelihood = (1.0 - pMissings) * pDiffs[i,j,:] * (1.0 - identPEP) * (1.0 - linkPEP) + (1.0 - pMissingGeomAvg[i]) * (pQuantIncorrectId[i][j] * identPEP * (1.0 - linkPEP) + linkPEP)
        
        if np.min(likelihood) == 0.0:
          likelihood += np.nextafter(0,1)
        #likelihood = np.nan_to_num(likelihood)
        '''
        print("""
              DEBUG MESSAGE!!!!!!!!!!!!!!!!!
              """)
        print("pMissings")
        print(pMissings)
        print("pDiffs[i,j,:]")
        print(pDiffs[i,j,:])
        print("identPEP")
        print(identPEP)
        print("linkPEP")
        print(linkPEP)
        print("pMissingGeomAvg[i]")
        print(pMissingGeomAvg[i])
        print("pQuantIncorrectId[i][j]")
        print(pQuantIncorrectId[i][j])
        print("""
              END OF MESSAGE!!!!!!!!!!
              """)
        '''
        if np.isnan(likelihood).sum() > 0:
            print("NaN count: " + str(np.isnan(likelihood).sum()))
            print(likelihood)
            raise ("NaN encountered in likelihood computations")
        pProteinQuant += np.log(likelihood)
        #pProteinQuant = np.nan_to_num(pProteinQuant) # fix NaN issue in protein quants
      
    pProteinQuant -= np.max(pProteinQuant)
    #print(pProteinQuant)
    pProteinQuant = np.exp(pProteinQuant) / np.sum(np.exp(pProteinQuant))
    pProteinQuantsList.append(pProteinQuant)
    
    #print(len(params["proteinQuantCandidates"]))
    eValue, confRegion = getPosteriorParams(params['proteinQuantCandidates'], pProteinQuant)
    #print(params['proteinQuantCandidates'])
    bayesQuantRow.append(eValue)
  
  return pProteinQuantsList, bayesQuantRow

def imputeValues(quantMatrix, proteinRatios, testProteinRatios):
  logIonizationEfficiencies = np.log10(quantMatrix) - np.log10(proteinRatios)
  #print(logIonizationEfficiencies)
  numNonZeros = np.count_nonzero(~np.isnan(logIonizationEfficiencies), axis = 1)[:,np.newaxis] - ~np.isnan(logIonizationEfficiencies)
  #print(numNonZeros)
  np.nan_to_num(logIonizationEfficiencies, False)
  meanLogIonEff = (np.nansum(logIonizationEfficiencies, axis = 1)[:,np.newaxis] - logIonizationEfficiencies) / numNonZeros
  
  logImputedVals = np.tile(meanLogIonEff[:, :, np.newaxis], (1, 1, len(testProteinRatios))) + testProteinRatios
  return logImputedVals

def pMissing(x, muLogit, sigmaLogit):
  return 1.0 - hyperparameters.logit(x, muLogit, sigmaLogit) + np.nextafter(0, 1)

def getPosteriorProteinGroupRatios(pProteinQuantsList, bayesQuantRow, params):
  numGroups = len(params["groups"])
  
  pProteinGroupQuants = list()
  for groupId in range(numGroups):
    filteredProteinQuantsList = np.array([x for j, x in enumerate(pProteinQuantsList) if j in params['groups'][groupId]])
    pDiffPrior = params['inGroupDiffPrior'][groupId]
    if "shapeInGroupStdevs" in params:
      #pMu = getPosteriorProteinGroupMuMarginalized(filteredProteinQuantsList, params)
      pMu = getPosteriorProteinGroupMuMarginalized(pDiffPrior, filteredProteinQuantsList, params)
    else:
      pMu = getPosteriorProteinGroupMu(params['inGroupDiffPrior'], filteredProteinQuantsList, params)
    pProteinGroupQuants.append(pMu)
  
  return pProteinGroupQuants
  
def getPosteriorProteinGroupMu(pDiffPrior, pProteinQuantsList, params):
  pMus = np.zeros_like(params['proteinQuantCandidates'])
  for pProteinQuants in pProteinQuantsList:
    pMus += np.log(np.convolve(pDiffPrior, pProteinQuants, mode = 'valid'))
  
  #pMus = np.nan_to_num(pMus)
  pMus -= np.max(pMus)
  pMus = np.exp(pMus) / np.sum(np.exp(pMus))
  return pMus

def getPosteriorProteinGroupMuMarginalized(pDiffPrior, pProteinQuantsList, params):
  pMus = np.zeros((len(params['sigmaCandidates']), len(params['proteinQuantCandidates'])))
  for pProteinQuants in pProteinQuantsList:
    for idx, pDiffPrior in enumerate(params['inGroupDiffPrior']):
      pMus[idx,:] += np.log(np.convolve(pDiffPrior, pProteinQuants, mode = 'valid'))
  
  pSigmas = hyperparameters.funcGamma(params['sigmaCandidates'], params["shapeInGroupStdevs"], params["scaleInGroupStdevs"]) # prior
  pMus = np.log(np.dot(pSigmas, np.exp(pMus)))
  
  pMus -= np.max(pMus)
  pMus = np.exp(pMus) / np.sum(np.exp(pMus))
  
  return pMus
  
def getProteinGroupsDiffPosteriors(pProteinGroupQuants, params):
  numGroups = len(params['groups'])  
  pProteinGroupDiffs, muGroupDiffs = dict(), dict()
  for groupId1, groupId2 in itertools.combinations(range(numGroups), 2):
    pDifference = np.convolve(pProteinGroupQuants[groupId1], pProteinGroupQuants[groupId2][::-1])
    pProteinGroupDiffs[(groupId1,groupId2)] = pDifference
    muGroupDiffs[(groupId1,groupId2)], _ = np.log2(getPosteriorParams(params['proteinDiffCandidates'], pDifference) + np.nextafter(0, 1))
  return pProteinGroupDiffs, muGroupDiffs
  
def getProbBelowFoldChangeDict(pProteinGroupDiffs, params):
  probsBelowFoldChange = dict()
  numGroups = len(params["groups"])
  for groupId1, groupId2 in itertools.combinations(range(numGroups), 2):
    probsBelowFoldChange[(groupId1, groupId2)] = getPosteriorProteinGroupDiff(pProteinGroupDiffs[(groupId1, groupId2)], params)
  #probsBelowFoldChange['ANOVA'] = getProbBelowFoldChangeANOVA(pProteinGroupQuants, params)
  return probsBelowFoldChange

def getPosteriorProteinGroupDiff(pDifference, params):  
  return sum([y for x, y in zip(params['proteinDiffCandidates'], pDifference) if abs(np.log2(10**x)) < params['foldChangeEval']])

# this is a "pseudo"-ANOVA test which calculates the probability distribution 
# for differences of means between multiple groups. With <=4 groups it seemed
# to return reasonable results, but with 10 groups it called many false positives.
def getProbBelowFoldChangeANOVA(pProteinGroupQuants, params):
  if len(pProteinGroupQuants) > 4:
    print("WARNING: this ANOVA-like test might not behave well if >4 treatment groups are present")
  
  if len(pProteinGroupQuants) >= 2:
    convProbs = convolution_dp.convolveProbs(pProteinGroupQuants)
    bandwidth = np.searchsorted(params['proteinQuantCandidates'], params['proteinQuantCandidates'][0] + np.log10(2**params['foldChangeEval']))
    probBelowFoldChange = 0.0
    for i in range(bandwidth):
      probBelowFoldChange += np.trace(convProbs, offset = i)
  else:
    probBelowFoldChange = 1.0
  return min([1.0, probBelowFoldChange])
  
def getPosteriorParams(proteinQuantCandidates, pProteinQuants):
  return 10**np.sum(proteinQuantCandidates * pProteinQuants), 0.0
  if False:
    eValue, variance = 0.0, 0.0
    for proteinRatio, pq in zip(proteinQuantCandidates, pProteinQuants):
      if pq > 0.001:
        #print(10**proteinRatio, pq)
        eValue += proteinRatio * pq

    for proteinRatio, pq in zip(proteinQuantCandidates, pProteinQuants):
      if pq > 0.001:
        variance += pq * (proteinRatio - eValue)**2
    eValueNew = 10**eValue
    
    return eValueNew, [10**(eValue - np.sqrt(variance)), 10**(eValue + np.sqrt(variance))]

