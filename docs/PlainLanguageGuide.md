# Plain Language Guide

This document explains the project without technical terms.

## What is the project?
Drug companies want to know early if a drug will get approval. In 2019, a team at
Novartis built a good model for this task. That model used private data. We cannot use
that data.

This project rebuilds the method with public data. It uses data from ClinicalTrials.gov
and from open outcome datasets. It answers two questions:
1. Can the method match or beat the best public result?
2. Does the method still work on recent trials from 2020 to 2024?

## How does it work?
1. The code reads trials from public datasets into one common format.
2. The code turns each trial into numbers (its phase, its length, and if it stopped early).
3. The code separates past trials from future trials. The model learns from past trials.
   The model is tested on future trials. This stops the model from seeing the future.
4. The code trains models and compares the score to the public score on the same test.

## Why is the honesty important?
We do not claim to beat the old private-data score directly. That would compare two
different tests. We claim a win only on the same public test. We also mark which data an
expert checked and which data the code estimated.
