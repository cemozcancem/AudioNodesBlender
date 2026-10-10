# Third-Party Notices

Audio Nodes is licensed under the [GNU General Public License v3.0](LICENSE).
It builds on the following third-party software, each under its own license.

## Linked into the audio engine

| Component | Version | License | Source |
|---|---|---|---|
| JUCE | 8.0.4 | AGPLv3 (or commercial JUCE license) | https://github.com/juce-framework/JUCE |
| cppzmq | 4.10.0 | MIT | https://github.com/zeromq/cppzmq |
| nlohmann/json | 3.11.3 | MIT | https://github.com/nlohmann/json |

JUCE is used under the AGPLv3. GPLv3 section 13 allows combining GPLv3 code
with AGPLv3 code; the combined engine binary is therefore distributed under
the terms of the AGPLv3. The complete corresponding source is this repository.

## Bundled in the macOS release (`AudioNodesEngine.app/Contents/Frameworks/`)

| Component | Version | License | Source |
|---|---|---|---|
| libzmq | 4.3.5 | MPL-2.0 | https://github.com/zeromq/libzmq |
| libsodium | 1.0.21 | ISC | https://github.com/jedisct1/libsodium |

These are unmodified builds. Their source code is available at the links above.

## Installed at runtime into Blender's Python

| Component | License | Source |
|---|---|---|
| pyzmq | BSD-3-Clause | https://github.com/zeromq/pyzmq |

## License texts

- AGPLv3: https://www.gnu.org/licenses/agpl-3.0.txt
- MIT: https://opensource.org/license/mit
- MPL-2.0: https://www.mozilla.org/en-US/MPL/2.0/
- ISC: https://opensource.org/license/isc-license-txt
- BSD-3-Clause: https://opensource.org/license/bsd-3-clause
