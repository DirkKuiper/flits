# Changelog

All notable changes to FLITS will be documented in this file.

This file is managed by Release Please.

## [0.9.0](https://github.com/DirkKuiper/flits/compare/flits-v0.8.0...flits-v0.9.0) (2026-07-11)


### Features

* **polarization:** complete RM synthesis workflow ([823bbd1](https://github.com/DirkKuiper/flits/commit/823bbd15aec25c1626251f7c9b50185d7ab39f23))

## [0.8.0](https://github.com/DirkKuiper/flits/compare/flits-v0.7.3...flits-v0.8.0) (2026-07-11)


### Features

* **analysis:** add weighted RM synthesis ([dd7d89d](https://github.com/DirkKuiper/flits/commit/dd7d89df4f3e54fa2d1004836c1cc1668f6135d3))
* **analysis:** automatic burst localization in time and frequency ([6c271b3](https://github.com/DirkKuiper/flits/commit/6c271b321295225307cc0fe5952645a1d04211d0))
* **polarization:** expand RM synthesis workflow ([c61bb47](https://github.com/DirkKuiper/flits/commit/c61bb47fc5bf66ea3ba1e87098eb564b113284ca))
* **web:** Auto Localize action; tighten event boundaries ([d5e3823](https://github.com/DirkKuiper/flits/commit/d5e3823613de1533f93015c911c1fd5fa63161c8))


### Bug Fixes

* **analysis:** calibrate matched-filter scales against red noise ([7158dd2](https://github.com/DirkKuiper/flits/commit/7158dd25a968704c797c54a10cb88907c48d3112))
* **io:** correct CHIME catalog time units ([f4c3556](https://github.com/DirkKuiper/flits/commit/f4c35569a2a42a7916f7daf3cafe88be5bc177be))
* **measurements:** noise-calibrated saturation diagnostic ([c4f9ec5](https://github.com/DirkKuiper/flits/commit/c4f9ec547fc4b33cb2fd1f97ea9f87ded3e85ada))
* **web:** harden desktop session workflow ([#83](https://github.com/DirkKuiper/flits/issues/83)) ([3eaedc3](https://github.com/DirkKuiper/flits/commit/3eaedc3b2321ae3ab669a9aedad2a9faa2210e8d))
* **web:** improve spectral window contrast ([6e1f1c4](https://github.com/DirkKuiper/flits/commit/6e1f1c4055d747da765a37cf6e6523daa23e0ebf))


### Performance Improvements

* **analysis:** keep input precision in burst localization ([dd83bb1](https://github.com/DirkKuiper/flits/commit/dd83bb167a39dc0c6b05a3cef901b9862d445e32))


### Documentation

* document automatic burst localization ([d3fc57e](https://github.com/DirkKuiper/flits/commit/d3fc57e8db440e0145cb8fa2c9be70a2f5fe8af6))
* mention automatic burst localization in README highlights ([c3f5e73](https://github.com/DirkKuiper/flits/commit/c3f5e730e04a7f9af58163466778c22c6409dd8a))

## [0.7.3](https://github.com/DirkKuiper/flits/compare/flits-v0.7.2...flits-v0.7.3) (2026-06-19)


### Bug Fixes

* **io:** support psrfits archive extension ([9b0704a](https://github.com/DirkKuiper/flits/commit/9b0704af84542d05ed64347f16a5493574b17704))
* **web:** speed up mounted file listing ([1083878](https://github.com/DirkKuiper/flits/commit/10838780e803f4ef559b9ad395fc0ed8615f892e))

## [0.7.2](https://github.com/DirkKuiper/flits/compare/flits-v0.7.1...flits-v0.7.2) (2026-06-11)


### Bug Fixes

* **web:** preserve native viewer plots ([4042ded](https://github.com/DirkKuiper/flits/commit/4042dedd85836c24779c9b9e79f5092715401295))

## [0.7.1](https://github.com/DirkKuiper/flits/compare/flits-v0.7.0...flits-v0.7.1) (2026-05-28)


### Bug Fixes

* preserve DM sweep after applying best DM ([bc5f08e](https://github.com/DirkKuiper/flits/commit/bc5f08e9ad1467f60847fab83a22ad97ae4e8673))

## [0.7.0](https://github.com/DirkKuiper/flits/compare/flits-v0.6.0...flits-v0.7.0) (2026-05-21)


### Features

* **model-fit:** add explicit fitting config and solver budget ([044eb9a](https://github.com/DirkKuiper/flits/commit/044eb9aae581165c09bcbf656bba738f23ae11df))
* **web:** add parameter-based model fit controls ([f56c1c3](https://github.com/DirkKuiper/flits/commit/f56c1c313f24eb3cf0ae2ef0b836e6d085928ea3))


### Documentation

* **model-fit:** document interactive model fitting ([3910260](https://github.com/DirkKuiper/flits/commit/39102604b7e33edf7cc60f6cc20e588a379bf469))

## [0.6.0](https://github.com/DirkKuiper/flits/compare/flits-v0.5.0...flits-v0.6.0) (2026-05-12)


### Features

* support folded PSRFITS pseudo-time loading ([3423193](https://github.com/DirkKuiper/flits/commit/3423193912b22e1f9fdabb060495e4ba5a781932))

## [0.5.0](https://github.com/DirkKuiper/flits/compare/flits-v0.4.0...flits-v0.5.0) (2026-05-01)


### Features

* add fitburst fit profiles ([f5ae324](https://github.com/DirkKuiper/flits/commit/f5ae324dbf597c13c268d233f84ae1cbf6f3443d)), closes [#46](https://github.com/DirkKuiper/flits/issues/46)
* add fitburst model control config ([073d9e8](https://github.com/DirkKuiper/flits/commit/073d9e83860774bf0f42e11cbf149931fa23efc1)), closes [#47](https://github.com/DirkKuiper/flits/issues/47)
* add fitburst scintillation mode ([c8027f0](https://github.com/DirkKuiper/flits/commit/c8027f0382c70b50dde932e56aca8d520e916e67)), closes [#49](https://github.com/DirkKuiper/flits/issues/49)
* add iterative fitburst fitting ([55cccab](https://github.com/DirkKuiper/flits/commit/55cccab63c8dd058a3b98132ccc518d9bfc977c4)), closes [#42](https://github.com/DirkKuiper/flits/issues/42)
* add weighted fitburst fitting support ([5536246](https://github.com/DirkKuiper/flits/commit/5536246d8e24f24d904b6f8f4f5fc3666c406531)), closes [#41](https://github.com/DirkKuiper/flits/issues/41)
* capture fitburst failure diagnostics ([08e791a](https://github.com/DirkKuiper/flits/commit/08e791ae4859ff872b2567216f77707146854f3f)), closes [#45](https://github.com/DirkKuiper/flits/issues/45)
* reuse previous fit for fitburst initialization ([fb2d805](https://github.com/DirkKuiper/flits/commit/fb2d805ec7e3ebf3d521dab4076878fd8a83f3bc)), closes [#48](https://github.com/DirkKuiper/flits/issues/48)


### Bug Fixes

* drop unsupported fitburst bounds config ([ef04699](https://github.com/DirkKuiper/flits/commit/ef046993aa95a275a8b8ebc1996424b02d53a654)), closes [#43](https://github.com/DirkKuiper/flits/issues/43)
* validate fitburst fixed parameters ([d1fe472](https://github.com/DirkKuiper/flits/commit/d1fe472b3b8ae5a49301eaeb4e65ce6a36f0b4c4)), closes [#44](https://github.com/DirkKuiper/flits/issues/44)


### Documentation

* refresh fitburst scattering fit guide ([b42f524](https://github.com/DirkKuiper/flits/commit/b42f524bbbda3d1d24a78aa62a6b6cb7248f695a)), closes [#50](https://github.com/DirkKuiper/flits/issues/50)

## [0.4.0](https://github.com/DirkKuiper/flits/compare/flits-v0.3.0...flits-v0.4.0) (2026-04-30)


### Features

* **session:** add saved session library and portable snapshots ([89d324f](https://github.com/DirkKuiper/flits/commit/89d324fb6146b706c9c8c59e37906d8f3cccc418))

## [0.3.0](https://github.com/DirkKuiper/flits/compare/flits-v0.2.1...flits-v0.3.0) (2026-04-23)


### Features

* add paper-grade arrival timing and workspace UI ([c108289](https://github.com/DirkKuiper/flits/commit/c108289005f24c51ce1b978bd31d675dea417569))

## [0.2.1](https://github.com/DirkKuiper/flits/compare/flits-v0.2.0...flits-v0.2.1) (2026-04-22)


### Bug Fixes

* uncertainties are now reported in more detail ([#28](https://github.com/DirkKuiper/flits/issues/28)) ([bc3deac](https://github.com/DirkKuiper/flits/commit/bc3deac3d31f9c0160f1d7edcfd80c6b5a0c9e26))

## [0.2.0](https://github.com/DirkKuiper/flits/compare/flits-v0.1.1...flits-v0.2.0) (2026-04-21)


### Features

* add I/O support for CHIME data cat1 and basecat1 ([1cc32b6](https://github.com/DirkKuiper/flits/commit/1cc32b6b3fd79fa4b49fe4786a76baf635ac97be))

## [0.1.1] - 2026-04-18

- Published the first PyPI release workflow and packaging metadata cleanup.

## [0.1.0] - 2026-03-10

- Initial public release.
