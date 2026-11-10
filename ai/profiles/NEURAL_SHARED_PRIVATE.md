# MILG neural engine v2

This version actually trains neural-network parameters. It is a new experimental
engine architecture, not evidence of a novel scientific algorithm or a validated
manufacturing foundation model. Version 1 CumulativeModel and all deployed weights
remain available unchanged until an explicit gated installation.

## Architecture

- Each process has a private MLP, input -> 64 -> 32 -> binary logit.
- Every measured feature retains its own signed standardized position and missing
  indicator. No reduction to only one maximum deviation per physical quantity.
- Real-process input projectors map their differing inputs into 32 dimensions.
- A learned shared MLP maps 32 -> 32 -> 16, followed by per-process residual heads.
- Private models train first and are frozen while shared parameters learn.
- Residual heads start at zero. Validation selects a probability mixing weight
  from {0, .25, .5, .75, 1}; zero retains that run's private model.
- Synthetic CausalMan trains a private model only. Its examples are excluded from
  the shared optimizer. M2/M3 has no verified defect targets and is not included.

There are no claimed causal effects, joint-strength guarantees or automatic
machine-control actions. Experimental conditions, equipment judgement, machining
wear and robot insertion anomalies must not be called one interchangeable label.
The current shared encoder receives real milling and screw-driving datasets.
This does not establish knowledge of unseen manufacturing processes or real
industrial interference press-fit operations.

Screw recipe v2 retains the 32 original stage summaries and adds 24 ordered torque
samples per stage, four gradient statistics, duration and torque-rate RMS. Total:
152 features. It uses measured sequence order, not an angle sort that could hide
reversals. Absent signals/stages are marked missing; no fabricated normal labels.
These are resampled curve features, not end-to-end learning on all raw samples.

## Run

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\dksgu\Desktop\code\mlng-mes\mlng-mes\tools\Train-MilgSharedPrivate.ps1"
```

Optional `-ScrewDir`, `-ExtraDir`, `-MctDir` override the data paths. CPU training
defaults to four threads, up to 40 epochs, early stopping patience six. Tensor
batch size is 256. Local checkpoints use validation binary cross-entropy; shared
checkpoint uses macro validation BCE across real processes. Synthetic batches
never enter this loss. No test predictions are computed during training.

Artifacts:

- `candidate.json`: architecture, actual tensor weights, frozen input recipes,
  thresholds and process mixing policies.
- `training.json`: validation learning curves and shared-encoder weight change.
- `validation.md`: per-process validation results and the pre-test readiness gate.
- `frozen.json`: candidate SHA-256 and evaluation snapshot hashes.
- `*.npz`: exact numeric inputs and grouped partitions for reproducible evaluation;
  read with allow_pickle=False.
- `manifest.json`: source audit and training configuration.

After training, separately run:

```powershell
python -X utf8 -u -m mlng_ai.shared_private evaluate --run "ABSOLUTE_RESULT_DIRECTORY"
```

Or pass `-Evaluate` to the PowerShell wrapper to execute this as a second stage
after freezing. Re-evaluating the same frozen run is refused; read its existing
evaluation.json instead. This guards checkpoint integrity, not secrecy: these
public test datasets were already inspected in previous experiments and are not
a new blind field test. A new timestamp also does not create new test data.
If validation AUC is below .6 or validation recall is zero in any process, the
evaluate command records `blocked_before_test` and does not score the test set.

The separate evaluation compares shared/private predictions with private-only
neural models using the same full features and partitions. It does not prove
noninferiority to older on-disk sklearn/statistical models or independent factory
performance. Hyperparameters/mixture weights must not be retuned on these test
results. Existing per-tool/per-machine coverage limitations still apply.

`-Evaluate -Install` installs only a passing deployment.json. Failure leaves old
engine routes intact. Local state defaults to ai/state. Server tenants require the
actual <AI_STATE_DIR>/tenants/<tenant> path and deployed PyTorch dependencies.
The wrapper also verifies raw-input automatic inference after installation.
No server/container/GitHub Pages deployment is performed by local training.

Business-plan statements such as tenant adaptation, real-factory generalization,
security guarantees and commercial performance require corresponding implemented
workflows and external evidence. This prototype alone does not establish them.
