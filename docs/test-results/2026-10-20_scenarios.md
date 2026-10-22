# Digital twin — scenario results

| time (house) | scenario | result | minutes | detail |
|---|---|---|---|---|
| 23:03 | Forgot the light | PASS | 7 | AI switched it off 5.5 min after you left (5 min for lights, x0.5..x2 from the model; last seen 5.0 min); radar and PIR both empty |
| 23:26 | Sitting still | PASS | 15 | light stayed on for 15 min although the PIR saw no movement (radar confirmed you) |
| 23:54 | Pre-cooling, nobody came | PASS | 20 | switched on at 0 min, nobody came, switched off at 20 min and recorded as a wrong prediction |
| 00:12 | Pre-cooling, you came | PASS | 10 | on before you arrived, still on, counted as a correct prediction |
| 21:12 | Lights never early | PASS | 12 | stayed off for 12 min while the room was empty, on 0.3 min after you walked in (ai) |
| 21:19 | Question withdrawn | PASS | 1 | question withdrawn 0.3 min after you left (the room changed) |
| 21:27 | Hands off after manual control | FAIL | 1 | the AI still planned 'learning' |
| 21:38 | Fan drawing double power | PASS | 3 | alert after 3.2 min: Living room fan drawing 2.2x its usual power |
| 01:41 | Fell asleep with the light on | PASS | 233 | night 1: asked after 10 min, you said yes; night 2: asked after 0 min, you said yes; night 3: asked after 0 min, you said yes; night 4: switched off by itself after 0 min and told you |
| 06:01 | A whole day | PASS | 1442 | AI switched 8 on and 1 off: lights on as you walked in 6, lights off as you lay down 0, pre-cooling 2 (came 2, missed 0), skipped by sensors 0, smart off 1 (~1.23 Wh saved, 0.0 Wh used by misses), asked 0 (expired 0). Rules: on 0, off 3. By hand: on 5, off 9. |
| 08:41 | Hands off after manual control | PASS | 123 | AI left it alone for 2 h after the button, then took part again |
| 10:55 | Hands off after manual control | PASS | 123 | AI left it alone for 2 h after the button, then took part again |
| 11:16 | Forgot the light | PASS | 4 | AI switched it off 3.0 min after you left (5 min for lights, x0.5..x2 from the model; last seen 2.5 min (x0.5: the model says you usually don't need it now)); radar and PIR both empty |
| 11:40 | Sitting still | PASS | 15 | light stayed on for 15 min although the PIR saw no movement (radar confirmed you) |
| 12:08 | Pre-cooling, nobody came | PASS | 20 | switched on at 0 min, nobody came, switched off at 20 min and recorded as a wrong prediction |
| 12:27 | Pre-cooling, you came | PASS | 10 | on before you arrived, still on, counted as a correct prediction |
| 21:12 | Lights never early | PASS | 13 | stayed off for 12 min while the room was empty, on 0.3 min after you walked in (ai) |
| 21:24 | Question withdrawn | PASS | 1 | question withdrawn 0.3 min after you left (the room changed) |
| 23:34 | Hands off after manual control | PASS | 123 | AI left it alone for 2 h after the button, then took part again |
| 23:48 | Fan drawing double power | PASS | 3 | alert after 3.2 min: Living room fan drawing 2.1x its usual power |
| 01:31 | Fell asleep with the light on | PASS | 92 | night 1: asked after 0 min, you said yes; night 2: asked after 1 min, you said yes; night 3: asked after 0 min, you said yes; night 4: switched off by itself after 0 min and told you |
| 06:01 | A whole day | PASS | 1442 | AI switched 9 on and 2 off: lights on as you walked in 7, lights off as you lay down 1, pre-cooling 2 (came 2, missed 0), skipped by sensors 1, smart off 1 (~1.17 Wh saved, 0.0 Wh used by misses), asked 0 (expired 0). Rules: on 0, off 3. By hand: on 4, off 8. |
