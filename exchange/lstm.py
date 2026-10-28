"""Small trainable LSTM regressor, with full backpropagation through time (NumPy)."""
import numpy as np


def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -30, 30)))


class LSTM:
    def __init__(self, hidden=8, seed=7):
        self.hidden = hidden
        rng = np.random.default_rng(seed)
        self.w = rng.normal(0, 0.12, (4 * hidden, hidden + 1))
        self.b = np.zeros(4 * hidden)
        self.b[:hidden] = 1  # forget bias
        self.yw = rng.normal(0, 0.12, hidden)
        self.yb = np.zeros(1)

    def forward(self, sequence):
        h = np.zeros(self.hidden)
        c = np.zeros(self.hidden)
        cache = []
        for x in sequence:
            z = np.r_[h, x]
            a = self.w @ z + self.b
            f, i, o = (sigmoid(v) for v in np.split(a[:3 * self.hidden], 3))
            g = np.tanh(a[3 * self.hidden:])
            previous = c
            c = f * c + i * g
            h = o * np.tanh(c)
            cache.append((z, f, i, o, g, previous, c))
        return float(self.yw @ h + self.yb[0]), h, cache

    def train(self, samples, epochs=12, lr=0.015):
        # Online SGD over chronological windows. Test windows never enter training.
        for _ in range(epochs):
            for sequence, target in samples:
                prediction, h, cache = self.forward(sequence)
                error = np.clip(prediction - target, -5, 5)
                dw, db = np.zeros_like(self.w), np.zeros_like(self.b)
                dyw = error * h
                dh = error * self.yw
                dc = np.zeros(self.hidden)
                for z, f, i, o, g, previous, c in reversed(cache):
                    tanh = np.tanh(c)
                    dc = dc + dh * o * (1 - tanh ** 2)
                    da = np.r_[dc * previous * f * (1 - f), dc * g * i * (1 - i), dh * tanh * o * (1 - o), dc * i * (1 - g ** 2)]
                    dw += np.outer(da, z)
                    db += da
                    dh = (self.w.T @ da)[:self.hidden]
                    dc = dc * f
                self.w -= lr * np.clip(dw, -1, 1)
                self.b -= lr * np.clip(db, -1, 1)
                self.yw -= lr * np.clip(dyw, -1, 1)
                self.yb -= lr * error

    def predict(self, sequence):
        return self.forward(sequence)[0]
