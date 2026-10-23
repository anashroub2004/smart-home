# Digital twin — scenario results

| time (house) | scenario | result | minutes | detail |
|---|---|---|---|---|
| 00:34 | Forgot the light | PASS | 4 | AI switched it off 3.1 min after you left (5 min for lights, x0.5..x2 from the model; last seen 2.5 min (x0.5: the model says you usually don't need it now)); radar and PIR both empty |
| 00:45 | Sitting still | FAIL | 11 | switched off after 10.6 min while you were there |
| 01:36 | Forgot the light | PASS | 4 | AI switched it off 3.1 min after you left (5 min for lights, x0.5..x2 from the model; last seen 2.5 min (x0.5: the model says you usually don't need it now)); radar and PIR both empty |
| 01:51 | Sitting still | PASS | 15 | light stayed on for 15 min although the PIR saw no movement (radar confirmed you) |
| 02:12 | Pre-cooling, nobody came | PASS | 20 | switched on at 1 min, nobody came, switched off at 20 min and recorded as a wrong prediction |
| 02:23 | Pre-cooling, you came | PASS | 10 | on before you arrived, still on, counted as a correct prediction |
| 21:12 | Lights never early | PASS | 13 | stayed off for 12 min while the room was empty, on 0.5 min after you walked in (ai) |
| 21:14 | Question withdrawn | PASS | 1 | question withdrawn 0.3 min after you left (the room changed) |
| 23:17 | Hands off after manual control | PASS | 123 | AI left it alone for 2 h after the button, then took part again |
| 23:21 | Fan drawing double power | PASS | 4 | alert after 3.4 min: Living room fan drawing 2.1x its usual power |
| 01:33 | Fell asleep with the light on | PASS | 132 | night 1: asked after 1 min, you said yes; night 2: asked after 0 min, you said yes; night 3: asked after 0 min, you said yes; night 4: switched off by itself after 1 min and told you |
| 06:01 | A whole day | PASS | 1441 | AI switched 8 on and 3 off: lights on as you walked in 6, lights off as you lay down 1, pre-cooling 2 (came 2, missed 0), skipped by sensors 0, smart off 2 (~2.4 Wh saved, 0.0 Wh used by misses), asked 0 (expired 0). Rules: on 0, off 8. By hand: on 8, off 6. |
| 06:30 | Forgot the light | PASS | 4 | AI switched it off 3.0 min after you left (5 min for lights, x0.5..x2 from the model; last seen 2.5 min (x0.5: the model says you usually don't need it now)); radar and PIR both empty |
| 06:54 | Sitting still | PASS | 15 | light stayed on for 15 min although the PIR saw no movement (radar confirmed you) |
| 07:23 | Pre-cooling, nobody came | PASS | 20 | switched on at 0 min, nobody came, switched off at 20 min and recorded as a wrong prediction |
| 07:41 | Pre-cooling, you came | PASS | 10 | on before you arrived, still on, counted as a correct prediction |
| 21:12 | Lights never early | PASS | 12 | stayed off for 12 min while the room was empty, on 0.3 min after you walked in (ai) |
| 21:24 | Question withdrawn | PASS | 2 | question withdrawn 0.3 min after you left (the room changed) |
| 23:33 | Hands off after manual control | PASS | 123 | AI left it alone for 2 h after the button, then took part again |
| 23:43 | Fan drawing double power | PASS | 3 | alert after 3.2 min: Living room fan drawing 2.1x its usual power |
| 01:31 | Fell asleep with the light on | PASS | 97 | night 1: asked after 0 min, you said yes; night 2: asked after 1 min, you said yes; night 3: asked after 0 min, you said yes; night 4: switched off by itself after 0 min and told you |
| 06:02 | A whole day | PASS | 1443 | AI switched 9 on and 2 off: lights on as you walked in 7, lights off as you lay down 0, pre-cooling 2 (came 2, missed 0), skipped by sensors 0, smart off 2 (~2.4 Wh saved, 0.0 Wh used by misses), asked 0 (expired 0). Rules: on 1, off 1. By hand: on 1, off 8. |
