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

## The dial without custom firmware

The Voice Preview Edition's ring could step through the stations. On stock
firmware it only sends volume changes, and fast turns arrive as one larger step
while turns at volume 0 or 1 send nothing, so tuning by dial on stock firmware
is a design that has been measured but not built. The custom-firmware overlay
in `esphome/` is opt-in and not flashed on the reference speaker.

## More than one speaker, measured

Home Assistant's load with one speaker tuned is about 0.7 % of a Raspberry
Pi 5's CPU. Two or more speakers have not been measured, because the reference
install has one.
