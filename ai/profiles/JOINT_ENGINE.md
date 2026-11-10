# MILG engine integration and common training

Run from PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\dksgu\Desktop\code\mlng-mes\mlng-mes\tools\Update-MilgEngine.ps1" -Install
```

Without `-Install`, the script only trains/evaluates candidates. Existing specialist
results are reused when present; missing results are trained. Shared coefficients
are rebuilt from replay inputs using `mlng_ai.model.CumulativeModel`, the existing
MILG implementation. This is not merely registering unrelated models. It is also
not raw-sequence deep learning or a manufacturing foundation model.

## Commands

`python -m mlng_ai.joint_engine build --mct-dir ROOT --screw-dir ROOT --extra-dir ROOT --out OUTPUT`
builds two common-coefficient models with identical train-only adapters and splits:

- Reference: HMoTP, NASA, Stiehl, synthetic CausalMan press-fit.
- Joint: reference datasets plus screw experimental conditions and AURSAD anomalies.

Both use shared beta plus process-specific delta. Batch weighting caps each
process's effective observations. Templates map only explicit measured columns
to physical-quantity deviations, spread and waveform trend/roughness slots.
The semantic recipe is intentionally compact and can lose information; its
success must be measured, not inferred from the presence of more training data.

Scaling/imputation are fitted on training rows only. Thresholds use validation
normal scores, with a default 5% false-positive target. HMoTP/NASA splits are by
tool; Stiehl by machine; CausalMan by batch; screw by workpiece; AURSAD by original
acquisition-order IDs after removing all duplicate seven-channel sequences.
The three AURSAD plate-thread anomalies do not survive the conservative duplicate
filter, and extra-component anomalies are absent from its chronological test.
No unseen-factory or physical screw/plate isolation claim is made.

The reference is a **same-recipe refit**, not the old on-disk model evaluated with
its original calibration. This comparison measures the effect of adding the two
new tasks, not equivalence to all historical accuracy claims. Public test data
have already been inspected; do not tune on repeated test results or call them
fresh blind trials.

## Installation and inference

`install-specialists` installs validation-selected local artifacts into immutable
copied bundles. `install-joint` requires the fixed regression gate to pass:
maximum AUC loss .01, recall loss .03, false-positive increase .02 for old tasks
on validation and test, plus validation AUC >= .6 and nonzero recall for new tasks.
These are engineering tolerances, not statistical proof of noninferiority.
If the joint gate fails, the wrapper retains old MCT/press-fit routes and installs
the separate validated screw/AURSAD specialists. Preload regression is installed
only when grouped CV beats the training-mean baseline.

`verify` passes real raw records through `Service.predict`, including automatic
process/contract selection. `predict --state STATE --process auto --input FILE.json
--out OUTPUT.json` is the reusable inference CLI. A raw input JSON is a list of
operation objects, not one row per sensor time point:

- Screw: original `tightening steps` with torque/angle arrays.
- AURSAD: seven equal-length arrays: `output_double_register_25` and
  `actual_TCP_force_0` through `_5`.
- Clamping: `torque_values` and `angle_values` arrays.
- MCT: original measured sensor-summary columns required by its saved adapter.
- Synthetic CausalMan: measured scalar columns and engineering bounds; must use
  explicit process `causalman_pressfit`, never automatic selection.

Multiple compatible routes cause abstention rather than guessing a model.
Outcome labels, status bits and result codes are not predictors. Regression output
uses `preload_kN`, not a fabricated failure probability. Joblib artifacts must come
from trusted local training; do not register downloaded executable model files.

`-StateDir` defaults to `ai/state` for the local Python engine. The web API uses
`<AI_STATE_DIR>/tenants/<tenant>`; pass that actual tenant state directory when
installing for a server tenant, and deploy the updated Python code/dependencies.
Running the local script does not deploy server containers or update GitHub Pages.
Existing `model.json` weights are not overwritten. Routes have a prior-registry
backup; use `Router(STATE).rollback()` to restore the preceding registry.

M2/M3's unlabeled raw curves and clamping preload's continuous targets are not
invented binary labels and are excluded from joint failure classification.
CausalMan is synthetic; joint predictions disclose synthetic shared training.
