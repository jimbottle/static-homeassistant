# Future improvements

What the first release leaves out, and why. Each of these is a choice, not
something forgotten. None is built without the owner saying so first.

## Synchronized multi-room groups

One station on several speakers at once, in step. Each speaker tuned today
gets its own stream and its own lead, and each keeps to the live moment on its
own, so two speakers on the same station are close but not sample-locked.
Locking them needs Music Assistant's grouping and a lead for the group rather
than per speaker, and has not been measured on any pair of speakers.

## Languages other than English

The voice sentences and every spoken reply are English. Another language needs
its own sentence file, written so that it captures nothing a home already says
(the rules at the top of `sentences/en.yaml`), and its own replies. Static's
station names stay as Static writes them in any language.

## The default HACS store

The first release installs as a HACS custom repository. The default store asks
for a track record of releases and use first, and Static agreed to an
unofficial build on conditions that a wider listing should be checked against
with them before it happens.

## A Music Assistant provider

Static as a source inside Music Assistant itself, beside its other radio
providers. It would reach every Music Assistant player without this
integration. It would also have to do what this integration does today: keep
Static's shared live timeline, start each episode partway through, and show the
Support link. That has not been designed, so for now the stream is made here
and handed to Music Assistant as an address.

## A shorter opening

A stream opens with 3.2 s the speaker can start on while the episode is
fetched. Music Assistant's source suggests about 2.2 s would do. Shortening it
needs measuring on the speaker in a quiet room, station by station, and it
saves about a second of a first tune.

## A better dial on stock firmware

The Voice Preview Edition's dial tunes on stock firmware after a double press
of its button, and that is built. It has limits the firmware sets: the device
sends only volume changes, so each click is read from one, the volume is put
back afterwards and its display flickers. A fast turn arrives as one larger
change and has not been checked by hand. The opt-in overlay in `esphome/` has
none of these limits, at the cost of running custom firmware
([docs/dial-firmware.md](docs/dial-firmware.md)). Doing better without it
needs the stock firmware to report a turn of the dial as an event of its own.

## More than one speaker, measured

Home Assistant's load with one speaker tuned is about 0.7 % of a Raspberry
Pi 5's CPU. Two or more speakers have not been measured, because the reference
install has one.
