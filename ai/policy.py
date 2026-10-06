"""Probability -> action. Pure functions, easy to test and to explain.

    p >= act and device off          schedule_on   (executed later, only if the sensor gate agrees)
    p >= act and device on           keep_on       (delays smart off: you will probably need it)
    suggest <= p < act, device off   suggest_on    (ask the user)
    p <= 0.20 and device on          suggest_off
    otherwise                        none
Hysteresis: once a device is "armed" (schedule_on / keep_on) it stays armed until p < act - 0.10,
so a probability wobbling around 0.80 does not flip the decision every 15 minutes.
Manual control in the last 2 h or a pause from the app always wins.
"""

ON_ACTIONS = ("schedule_on", "keep_on")


def decide(p, state_now, th, armed=False, paused=None):
    """th: dict(act, suggest, exit, off). paused: None | 'override' | 'user'. -> action string."""
    if paused == "override":
        return "paused_by_override"
    if paused == "user":
        return "paused_by_user"
    act_line = th["exit"] if armed else th["act"]
    if p >= act_line:
        return "keep_on" if state_now else "schedule_on"
    if not state_now and p >= th["suggest"]:
        return "suggest_on"
    if state_now and p <= th["off"]:
        return "suggest_off"
    return "none"
