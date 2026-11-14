# First release checklist

What has to be true before `v0.1.0`, who does each part, and where it stands.
A draft: the owner decides what a first release has to include.

"Owner" marks a step only the repo's owner can take: a merge, a repo setting,
the tag, or anything said to Static. Everything else is an agent's work
through a pull request.

## 1. The code is on `main`

- [ ] The station stream, tuning, voice handlers, recovery and media browser
      playback are merged, with CI green. Built and tested (490 tests) and
      running as a development copy; not merged as of this draft.
- [ ] Every review finding on those changes is fixed or answered, and logged
      in [review-findings.md](review-findings.md).
- [ ] No review is open on `main`.

## 2. It has been heard, not only tested

Heard so far, on a Home Assistant Voice Preview Edition through Music
Assistant: an MP3 station, an MP4 station, and the spoken station name
followed by the episode about two seconds later.

- [ ] Every station tuned once, each by its own name, by voice.
- [ ] Next and previous across the whole dial and back, with the right reply
      at each end.
- [ ] A bulletin heard where it falls, cut at 30 seconds.
- [ ] Stop, then play again: lands on the live moment, not where it stopped.
- [ ] A speaker that drops off the network while playing comes back on its
      station. Built; tested only against simulated speaker states.
- [ ] Two-hour listen across the schedule's daily changeover (about 7 a.m. US
      Central): no drift, no gap longer than the schedule's own.
- [ ] How long the stream's opening needs to be, measured on the speaker. It
      is 3.2 s; Music Assistant's source suggests 2.2 s would do.
- [ ] One hour of outgoing requests, logged and checked against the rules in
      [CLAUDE.md](../CLAUDE.md): only the allowed addresses, the User-Agent on
      every one, the schedule no more than every 15 minutes, counts no more
      than once a minute.

## 3. A stranger can install it

- [ ] A fresh Home Assistant goes from nothing to playing in under ten
      minutes using only the README.
- [ ] README: screenshots, and which Home Assistant, Music Assistant and
      speaker firmware versions it was tested on.
- [ ] The voice sentences: the README says how to switch them on, and what
      they will not capture ("next station" without "Static" is left alone).
- [ ] `FUTURE_IMPROVEMENTS.md` lists what the first release leaves out, each
      with its reason: multi-room groups, languages other than English, the
      default HACS store, a Music Assistant provider, a shorter opening.
- [ ] Owner: an icon for the integration that is this project's own. Static's
      logo is Static's. Then take `brands` out of the HACS check's ignore list
      in `ci.yml`.
- [ ] Owner: repo topics, for example `home-assistant`, `hacs`,
      `custom-integration`. Then take `topics` out of the same list.
- [ ] The `hacs` job in CI passes with nothing ignored.

## 4. Static

- [ ] Owner: the courtesy note describing the design, and the question about
      the Support link, sent to Static.
- [ ] Owner: anything Static asks for in reply is done, or the release waits.
- [ ] "unofficial" is in the repo description, the README title and the
      integration's name, and Static is credited and linked. True today.

## 5. The release itself

- [ ] Owner: `scripts/release.sh` pushes its release commit straight to
      `main`, which this repo's branch rules refuse. Decide how a release
      commit lands (through a pull request, or an exception for the owner)
      before the first one.
- [ ] Owner: `scripts/release.sh 0.1.0`. It sets the version in
      `manifest.json`, writes the changelog, tags `v0.1.0`, and the release
      workflow attaches the zip. Agents do not bump versions or tag.
- [ ] The zip installs through HACS as a custom repository on a Home
      Assistant that has never had the development copy.

## 6. After it is out

- [ ] The development copy on the reference install is replaced by the HACS
      install, recorded under that install's own rules.
- [ ] A short demo for Static of voice tuning, sent by the owner.
