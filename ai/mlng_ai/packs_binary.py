"""Serializable MILG binary specialist for public pack benchmarks."""
import numpy as np
from .model import CumulativeModel

class MilgBinaryModel:
    def fit(self, x, y):
        self.n_features_in_ = x.shape[1]
        self.model = CumulativeModel([f"feature_{i}" for i in range(x.shape[1])])
        self.model.add_process("candidate", (y.sum() + .5) / (len(y) + 1))
        self.model.update([("candidate", x, y, np.ones(len(y)))])
        return self

    def predict_proba(self, x):
        score = self.model.prob("candidate", self.model.predict("candidate", x, np.ones(len(x)))[0])
        return np.column_stack([1-score, score])
