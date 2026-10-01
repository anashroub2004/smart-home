import numpy as np, pandas as pd, time, pickle
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import f1_score, precision_score, recall_score

rng = np.random.default_rng(42)
DAYS, SLOT = 70, 15                # 10 weeks, 15-min slots
N = 96
rows = []
for d in range(DAYS):
    dow = d % 7; weekend = dow in (4, 5)          # Fri/Sat weekend
    j = lambda s: rng.normal(0, s)
    if not weekend:
        wake, leave, ret, sleep = 7+j(.3), 8+j(.3), 16.5+j(.7), 23+j(.5)
        out = [(leave, ret)]
    else:
        wake, sleep = 9.5+j(1), 23.8+j(.6)
        out = [(12+j(1), 17+j(1))] if rng.random() < .5 else []
    if rng.random() < .07: out = []                 # stays home unexpectedly
    tmax = 31 + j(2.5)
    sunset = 18.3 + j(.15)
    fan_b = fan_l = light_l = light_b = 0
    for s in range(N):
        h = s / 4
        home = not any(a <= h < b for a, b in out)
        if h < wake or h >= sleep: room = 'bed' if home else 'out'
        elif not home: room = 'out'
        else: room = 'bed' if rng.random() < .15 else 'liv'
        temp_out = tmax - 7 * np.cos((h - 15) / 24 * 2 * np.pi) - 7
        temp_b = temp_out + 1 + j(.4); temp_l = temp_out + j(.4)
        lux = max(0, 800 * np.sin(np.clip((h - 6) / (sunset - 6), 0, 1) * np.pi)) + j(15)
        # user behaviour (what the AI must learn)
        def fan(state, occ, t, asleep=False):
            if occ and t > 28.5 and rng.random() < .8: return 1
            if occ and asleep and t > 27: return 1
            if not occ and state and rng.random() < .8: return 0   # sometimes forgets
            if occ and t < 26.5: return 0
            return state
        asleep = h < wake or h >= sleep
        fan_b = fan(fan_b, room == 'bed', temp_b, asleep)
        fan_l = fan(fan_l, room == 'liv', temp_l)
        light_l = int(room == 'liv' and lux < 150 and rng.random() < .95)
        rows.append(dict(day=d, slot=s, dow=dow, weekend=int(weekend), temp_b=temp_b, temp_l=temp_l,
                         lux=lux, occ_b=int(room == 'bed'), occ_l=int(room == 'liv'),
                         fan_b=fan_b, fan_l=fan_l, light_l=light_l))
df = pd.DataFrame(rows)

def features(df, dev, occ, temp):
    X = pd.DataFrame({
        'h_sin': np.sin(df.slot / N * 2 * np.pi), 'h_cos': np.cos(df.slot / N * 2 * np.pi),
        'dow': df.dow, 'weekend': df.weekend, 'temp': df[temp], 'lux': df.lux, 'occ': df[occ],
        'now': df[dev], 'lag1': df[dev].shift(1), 'lag2': df[dev].shift(2),
        'yday': df[dev].shift(N), 'lweek': df[dev].shift(7 * N),
        'occ_yday_next': df[occ].shift(N - 4),
        'occ_run': df[occ].groupby((df[occ] != df[occ].shift()).cumsum()).cumcount(),
    })
    y = df[dev].shift(-4)                    # state in 60 min (4 slots ahead)
    return X, y

results = []
for dev, occ, temp in [('fan_b', 'occ_b', 'temp_b'), ('fan_l', 'occ_l', 'temp_l'), ('light_l', 'occ_l', 'temp_l')]:
    X, y = features(df, dev, occ, temp)
    ok = X.notna().all(1) & y.notna()
    X, y, D = X[ok], y[ok].astype(int), df.day[ok]
    tr, te = D < 56, D >= 56                  # 8 weeks train / 2 weeks test
    trans = (X.now[te] != y[te]).values        # moments the state changes
    def score(name, p, t_fit, t_pred, size):
        results.append(dict(device=dev, model=name, F1=f1_score(y[te], p), P=precision_score(y[te], p, zero_division=0),
                            R=recall_score(y[te], p), trans_acc=(p[trans] == y[te].values[trans]).mean(),
                            fit_s=t_fit, pred_ms=t_pred, size_kb=size))
    score('Baseline-yesterday', X.yday[te].values.astype(int), 0, 0, 0)
    score('Baseline-persist', X.now[te].values.astype(int), 0, 0, 0)
    models = {
        'RandomForest': RandomForestClassifier(300, min_samples_leaf=3, class_weight='balanced', n_jobs=-1, random_state=0),
        'GradBoost(LightGBM-type)': HistGradientBoostingClassifier(max_iter=300, learning_rate=.05, class_weight='balanced', random_state=0),
    }
    for name, m in models.items():
        t = time.time(); m.fit(X[tr], y[tr]); tf = time.time() - t
        t = time.time(); p = m.predict(X[te]); tp = (time.time() - t) / te.sum() * 1000
        score(name, p, tf, tp, len(pickle.dumps(m)) / 1024)
    # neural net on 4-hour window (stand-in for GRU/LSTM)
    W = 16
    arr = StandardScaler().fit(X[tr]).transform(X)
    seq = np.stack([np.roll(arr, k, axis=0) for k in range(W)], 1).reshape(len(arr), -1)
    valid = np.arange(len(arr)) >= W
    m = MLPClassifier((64, 32), max_iter=400, early_stopping=True, random_state=0)
    t = time.time(); m.fit(seq[tr.values & valid], y[tr.values & valid]); tf = time.time() - t
    t = time.time(); p = m.predict(seq[te.values]); tp = (time.time() - t) / te.sum() * 1000
    score('NeuralNet(seq, GRU stand-in)', p, tf, tp, len(pickle.dumps(m)) / 1024)

R = pd.DataFrame(results)
pd.set_option('display.width', 200)
print(R.round(3).to_string(index=False))
print('\nAVERAGE over devices:')
print(R.groupby('model')[['F1', 'P', 'R', 'trans_acc', 'fit_s', 'size_kb']].mean().round(3).sort_values('F1', ascending=False))
print('\nrows:', len(df), ' on-rate:', df[['fan_b', 'fan_l', 'light_l']].mean().round(2).to_dict())
