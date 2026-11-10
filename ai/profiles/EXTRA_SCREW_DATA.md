# Additional screw datasets

`python -m mlng_ai.extra_screw --data-dir ROOT --out OUTPUT` audits exactly:

- `AURSAD.h5`
- `Dataset on force and torque for fastener clamping.zip`
- `Datasets.zip` (M2/M3)

No recursive scan or automatic use of previous model files occurs. Keep original
ZIPs and previously trained data for reproducibility. Output uses a fresh timestamp.

Add `--train both` for two isolated baselines:

1. AURSAD: post-operation binary anomaly classifier from measured current torque
   and six measured TCP force/moment channels. Commands, achieved/target torque,
   status bits, result codes, absolute position, IDs and labels are excluded from
   predictors. Original sample ID defines fixed chronological 60/20/20 cutoffs.
   Full-run statistics and normalized-progress shapes are used, not raw-sequence
   deep learning. Threshold and candidate selection use validation only.
   Class 2 (extra component) and class 4 (damaged plate thread) are absent from
   this chronological test. This does not validate those types, unseen factories,
   unseen physical screws/holes, real joint strength or early warning.
2. Clamping: torque-curve to measured preload regression, fixed Ridge alpha=10.
   All three replicates of each of 16 DOE conditions are held out together during
   cross-validation. Scaling is fitted per training fold. Force curves and DOE
   outcomes never become predictors. Acquisition speed has conflicting source
   units and is excluded. Final candidate uses all 48 rows after evaluation.

M2/M3 has 83 raw curves but no independently verified defect/strength labels.
Only exact `Test Data` sheets are audited. GPR simulated sheets, calculated
tables and Gaussian coefficients are excluded. Raw curves contain exact
duplicates and angle reversals; source units require reconciliation. The module
does not train a quality classifier or claim all curves are healthy.

AURSAD has four within-run timestamp resets, all in the excluded loosening/picking
class 5. These are recorded in the audit; tightening runs must have monotone time.
The seven-channel measured input also has 484 exactly duplicated sequence pairs,
including 55 pairs with conflicting binary annotations. Every copy of every
nonunique sequence is excluded, regardless of labels. Conflicting labels are
counted for audit only, never used to repair labels or select a training copy.
Any nonfinite HDF numeric value, publisher checksum mismatch, ZIP CRC failure or
invalid curve causes audit failure instead of silently proceeding.

Artifacts retain sources. Publisher metadata lists CC BY 4.0; retain attribution.
No engine state, route registry, MCT, CausalMan or earlier screw model is modified.
