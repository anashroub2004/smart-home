# Digital twin — scenario results (branch ai/bedtime-prep-time, c48e55b)

The twin's own history had 4 nights of "light off after lying down" (fewer than 5), so `prep_min` = None and the
fixed 10 min was used — the fallback agreed for fewer than 5 nights.

| time (house) | scenario | result | minutes | detail |
|---|---|---|---|---|
| 15:41 | Forgot the light | PASS | 3 | AI switched it off 3.0 min after you left (5 min for lights, x0.5..x2 from the model; last seen 2.5 min (x0.5: the model says you usually don't need it now)); radar and PIR both empty |
| 15:56 | Sitting still | PASS | 15 | light stayed on for 15 min although the PIR saw no movement (radar confirmed you) |
| 16:17 | Pre-cooling, nobody came | PASS | 20 | switched on at 0 min, nobody came, switched off at 20 min and recorded as a wrong prediction |
| 16:28 | Pre-cooling, you came | PASS | 10 | on before you arrived, still on, counted as a correct prediction |
| 21:12 | Lights never early | PASS | 12 | stayed off for 12 min while the room was empty, on 0.3 min after you walked in (ai) |
| 21:14 | Question withdrawn | PASS | 1 | question withdrawn 0.3 min after you left (the room changed) |
| 23:18 | Hands off after manual control | PASS | 123 | AI left it alone for 2 h after the button, then took part again |
| 23:22 | Fan drawing double power | PASS | 3 | alert after 3.0 min: Living room fan drawing 2.1x its usual power |
| 01:42 | Fell asleep with the light on | PASS | 98 | night 1: asked after 11 min, you said yes; nights 2-3: asked after 0 min, you said yes; night 4: switched off by itself after 0 min and told you |
| 06:09 | A whole day | PASS | 1441 | AI switched 6 on and 1 off: lights on as you walked in 6, lights off as you lay down 0, smart off 1 (~1.17 Wh saved). Rules: on 0, off 1. By hand: on 2, off 7. |

**10 / 10 PASS.** `full_day` before this branch: lights on as you walked in 3, by hand on 4.
