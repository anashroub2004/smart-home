# Digital twin — scenario results

| time (house) | scenario | result | minutes | detail |
|---|---|---|---|---|
| 06:26 | Forgot the light | PASS | 4 | AI switched it off 2.8 min after you left (5 min for lights, x0.5..x2 from the model; last seen 2.5 min (x0.5: the model says you usually don't need it now)); radar and PIR both empty |
| 06:52 | Sitting still | PASS | 15 | light stayed on for 15 min although the PIR saw no movement (radar confirmed you) |
| 07:22 | Pre-cooling, nobody came | PASS | 20 | switched on at 0 min, nobody came, switched off at 20 min and recorded as a wrong prediction |
| 07:42 | Pre-cooling, you came | PASS | 10 | on before you arrived, still on, counted as a correct prediction |
| 21:12 | Lights never early | PASS | 13 | stayed off for 12 min while the room was empty, on 0.3 min after you walked in (ai) |
| 21:12 | Lights never early | PASS | 13 | stayed off for 12 min while the room was empty, on 0.3 min after you walked in (ai) |
| 21:23 | Question withdrawn | PASS | 2 | question withdrawn 0.3 min after you left (the room changed) |
| 21:32 | Hands off after manual control | FAIL | 2 | the AI still planned 'learning' |
| 21:43 | Fan drawing double power | PASS | 3 | alert after 3.0 min: Living room fan drawing 2.1x its usual power |
| 01:31 | Fell asleep with the light on | PASS | 216 | night 1: asked after 0 min, you said yes; night 2: asked after 1 min, you said yes; night 3: asked after 0 min, you said yes; night 4: switched off by itself after 0 min and told you |
|---|---|---|---|---|
| 06:03 | A whole day | PASS | 1444 | AI switched 9 on and 2 off: lights on as you walked in 9, lights off as you lay down 2, pre-cooling 0 (came 0, missed 0), skipped by sensors 0, smart off 0 (~0.0 Wh saved, 0.0 Wh used by misses), asked 0 (expired 0). Rules: on 0, off 2. By hand: on 4, off 10. |
| 08:23 | Hands off after manual control | PASS | 123 | AI left it alone for 2 h after the button, then took part again |
| 10:39 | Hands off after manual control | PASS | 124 | AI left it alone for 2 h after the button, then took part again |
