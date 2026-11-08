# Field checklist (no internet needed)

**Before leaving (indoors, once):** enroll Mitch from the page ("Enroll face"), or on the Pi with
`bash ~/Team-32/pi/field/enroll.sh`. The profile is kept across reboots.

1. **Power on.** Plug in the power bank (USB-C PD, 5V 5A). Everything starts by itself in about a minute.
2. **Join the hotspot.** On a phone: Wi-Fi network **THERMAL-PI**, password **thermal1234**. It appears about
   30 s after boot when no known Wi-Fi is around.
3. **Open the page:** http://10.42.0.1:8080
4. **Check sensors.** Thermal, microphones and both cameras should show green ("live").
5. **Site setup.** Measure the mount height and the direction the module faces, copy lat/lon from your phone's
   maps app, enter the camera tilt, then Save site.
6. **Clap test.** Press Clap test, then clap about 5 m away ON the module's 0 deg mark for the first few claps,
   then 90 deg to its left. Put the "body az mean" for the 0 deg claps into Mic az offset. If the 90 deg-left
   claps read about 270, tick "Mic left/right swapped". Save site again.
7. **Record.** Recording is on by default. Start makes a new run folder; Stop pauses saving (detection keeps running).
   Log scenario, time and distance on paper as you go.
8. **Watch.** The Verdict card shows people being tracked; "Rescuer messages" shows the decrypted 48-byte
   messages at the Nest, with the match level (3 = face verified as Mitch).
9. **Shut down.** Press Shut down on the page, or press the Pi's power button once. Unplug when the green light
   stops blinking.

Notes
- The thermal picture on the page only updates while recording is on.
- Enrolling or the clap test pauses detection for 10 to 30 s; the page shows the task's progress.
- Without the hotspot: plug in a keyboard and monitor and open http://localhost:8080.
- To change the hotspot password, set `HOTSPOT_PASS` in `eagle-hotspot.service` and reinstall.
