# Digital twin — scenario results

| time (house) | scenario | result | minutes | detail |
|---|---|---|---|---|
| 07:34 | Forgot the light | PASS | 12 | AI switched it off 10.5 min after you left (5 min for lights, x0.5..x2 from the model; last seen 10.0 min (x2: the model expects you back soon)); radar and PIR both empty |
| 07:57 | Sitting still | PASS | 15 | light stayed on for 15 min although the PIR saw no movement (radar confirmed you) |
| 08:26 | Pre-cooling, nobody came | PASS | 20 | switched on at 0 min, nobody came, switched off at 20 min and recorded as a wrong prediction |
| 08:41 | Pre-cooling, you came | PASS | 10 | on before you arrived, still on, counted as a correct prediction |
| 21:12 | Lights never early | PASS | 12 | stayed off for 12 min while the room was empty, on 0.3 min after you walked in (ai) |
| 21:25 | Question withdrawn | FAIL | 6 | question still open 5 min after you left |
| 23:35 | Hands off after manual control | PASS | 123 | AI left it alone for 2 h after the button, then took part again |
| 23:46 | Fan drawing double power | PASS | 3 | alert after 3.0 min: Living room fan drawing 2.1x its usual power |
| 01:31 | Fell asleep with the light on | PASS | 98 | night 1: asked after 0 min, you said yes; night 2: asked after 0 min, you said yes; night 3: asked after 0 min, you said yes; night 4: switched off by itself after 0 min and told you |
| 06:01 | A whole day | PASS | 1441 | AI switched 7 on and 1 off: lights on as you walked in 5, lights off as you lay down 0, pre-cooling 2 (came 2, missed 0), skipped by sensors 0, smart off 1 (~1.21 Wh saved, 0.0 Wh used by misses), asked 0 (expired 0). Rules: on 1, off 2. By hand: on 4, off 9. |
| 16:52 | Question withdrawn | FAIL | 6 | question still open 5 min after you left |
| 17:01 | Question withdrawn | FAIL | 0 | the AI refused to ask (sensors disagreed) |
| 17:11 | Question withdrawn | FAIL | 0 | the AI refused to ask (sensors disagreed) |
| 23:50 | Question withdrawn | PASS | 1 | question withdrawn 0.3 min after you left (the room changed) |
| 17:09 | Question withdrawn | PASS | 1 | question withdrawn 0.3 min after you left (the room changed) |

## After the suggestion_expiry fixes (80dab7c scenario + door exit in someone_home)

`suggestion_expiry` at 09:00, 13:00, 16:50, 17:00, 17:10, 21:25, 23:50, two rounds: **14 / 14 PASS**
(before: main and this branch both 4 / 14, the same runs failing on both).

| time (house) | scenario | result | minutes | detail |
|---|---|---|---|---|
| 00:55 | Forgot the light | PASS | 3 | AI switched it off 3.0 min after you left (5 min for lights, x0.5..x2 from the model; last seen 2.5 min (x0.5: the model says you usually don't need it now)); radar and PIR both empty |
| 01:10 | Sitting still | PASS | 15 | light stayed on for 15 min although the PIR saw no movement (radar confirmed you) |
| 01:30 | Pre-cooling, nobody came | PASS | 20 | switched on at 0 min, nobody came, switched off at 20 min and recorded as a wrong prediction |
| 02:11 | Pre-cooling, you came | FAIL | 40 | never switched on |
| 21:12 | Lights never early | PASS | 12 | stayed off for 12 min while the room was empty, on 0.3 min after you walked in (ai) |
| 21:14 | Question withdrawn | PASS | 1 | question withdrawn 0.3 min after you left (the room changed) |
| 23:17 | Hands off after manual control | PASS | 123 | AI left it alone for 2 h after the button, then took part again |
| 23:22 | Fan drawing double power | PASS | 3 | alert after 3.2 min: Living room fan drawing 2.0x its usual power |
| 01:41 | Fell asleep with the light on | PASS | 98 | night 1: asked after 11 min, you said yes; nights 2-3: asked after 0 min, you said yes; night 4: switched off by itself after 0 min and told you |
| 06:04 | A whole day | PASS | 1441 | AI switched 3 on and 1 off: lights on as you walked in 3, smart off 1 (~1.17 Wh saved). Rules: on 0, off 3. By hand: on 4, off 3. |

`guard_hit` re-run 7 times (08:52, 13:12, 17:42, 21:12, and at night after guard_miss: 02:22, 02:20): **7 / 7 PASS**.
The single FAIL at 02:11 could not be reproduced — open, not explained. The person was home and seen by
the radar, so the door-exit change (it only matters when no sensor sees anyone) is not expected to be the cause.
