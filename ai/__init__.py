"""Smart home AI (runs on the Raspberry Pi).

Modules:
    settings   constants and defaults (agreed decisions, see the smart-home-ai-model skill)
    spec       which devices the AI handles, built from /config (never hard-coded)
    store      SQLite readings on the Pi (raw data never leaves the house)
    features   15-minute slots -> feature table for one device
    train      nightly training: 8-week window, time-decay + energy-cost weights, evaluation, explanations
    presence   "will someone be home in 60 min?" model
    anomaly    energy fault detection from INA226 readings
    drift      routine-change detection
    energy     energy-scaled thresholds, adaptive offsets, waste ledger
    policy     probability -> action (act / suggest / nothing) with hysteresis
    gate       sensor checks right before acting, vacancy confirmation
    explain    human-readable "why" for each decision
    runtime    AIRuntime: plan every 15 min, tick every few seconds (gate, waste guard, smart off, expiry)
    dispatch   outputs: Firebase (through pi/firebase_writer.py) and MQTT
    synth      simulated household history (demo / simulator bootstrap)
    run        command line: demo | train | plan | devices | report
"""
