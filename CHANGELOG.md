# Changelog

All notable changes to FLITS will be documented in this file.

This file is managed by Release Please.

## [1.7.2](https://github.com/DirkKuiper/flits/compare/flits-v1.7.1...flits-v1.7.2) (2026-10-30)


### Documentation

* **testing:** describe when CI runs the platform checks ([aab5aba](https://github.com/DirkKuiper/flits/commit/aab5aba0d39795c76c7a92a26e8935bc8502e7c1))
* **testing:** describe when CI runs the platform checks ([66fcfec](https://github.com/DirkKuiper/flits/commit/66fcfec3ed5a279e1f3b663b6def25c625d6f317))

## [1.7.1](https://github.com/DirkKuiper/flits/compare/flits-v1.7.0...flits-v1.7.1) (2026-10-30)


### Bug Fixes

* **container:** require patched msgpack and document pip scanner exception ([264cee9](https://github.com/DirkKuiper/flits/commit/264cee944892e2db41aacc34aef3ebc4d5ce6212))
* **io:** read numeric PSRFITS declinations above 90 as packed ddmmss ([4ce1b56](https://github.com/DirkKuiper/flits/commit/4ce1b566c12bf9c90c4c8a1513fd2674ffdbd188))
* **io:** read numeric PSRFITS declinations above 90 as packed ddmmss ([13e15a2](https://github.com/DirkKuiper/flits/commit/13e15a24a33093194eda29a23383748a78af129b))
* **paper:** compare only the software's authors with the paper ([9b24a97](https://github.com/DirkKuiper/flits/commit/9b24a9755c86152a4dd75ccf4c2ab59de22b5a6c))


### Documentation

* **agents:** point the skill at FLITS 1.7 inspection and manifests ([4f0aaee](https://github.com/DirkKuiper/flits/commit/4f0aaee038f42a1e92ff8aa62a9d4a3f84deb59d))
* **agents:** shorten the skill's setup and point it at FLITS 1.7 ([8f3f836](https://github.com/DirkKuiper/flits/commit/8f3f836e39f87611aed3c5376a8d7db664f2d332))
* **agents:** shorten the skill's setup section ([e2e0ee3](https://github.com/DirkKuiper/flits/commit/e2e0ee34ae987dc4c277faeb18bd9c77b5173960))
* **citation:** make the arXiv paper the preferred citation ([b878053](https://github.com/DirkKuiper/flits/commit/b87805384eb953e8e433da205f3e61da96033c00))
* **citation:** make the arXiv paper the preferred citation ([ee77dcb](https://github.com/DirkKuiper/flits/commit/ee77dcbfc19a29b06246cc30c22492bcecc30214))
* **installation:** run FLITS in a Slurm job and tunnel to the worker ([6b293f2](https://github.com/DirkKuiper/flits/commit/6b293f2293eaff03915ac139d562a897640eaa3c))
* **installation:** run FLITS in a Slurm job and tunnel to the worker ([ad0b4da](https://github.com/DirkKuiper/flits/commit/ad0b4dab747270817ccf7761fc5be9f1df37712d))
* link the arXiv preprint and update container security checks ([#149](https://github.com/DirkKuiper/flits/issues/149)) ([4422e67](https://github.com/DirkKuiper/flits/commit/4422e679a29619dd9366b6e7df96b3ad1e7403f8))
* link the FLITS arXiv preprint ([5ca5fe4](https://github.com/DirkKuiper/flits/commit/5ca5fe440e99f66903471e3f929b10b1a3fba570))

## [1.7.0](https://github.com/DirkKuiper/flits/compare/flits-v1.6.0...flits-v1.7.0) (2026-10-27)


### Features

* **agents:** add portable installation and routing evaluations ([1ed5888](https://github.com/DirkKuiper/flits/commit/1ed58885887d37fee35dc2d3ffad67bafd002728))
* **cli:** add per-burst manifests and versioned output schemas ([4ade69c](https://github.com/DirkKuiper/flits/commit/4ade69c3bce75380fcbe3919bccd35387470cc14))
* **cli:** expose runtime identity and metadata inspection ([b5926e3](https://github.com/DirkKuiper/flits/commit/b5926e3a46326b9f80090d13fd8abdbac0b7c80d))
* **fitting:** pad the fit window, resolve narrow bursts, compare component counts ([07f8a8e](https://github.com/DirkKuiper/flits/commit/07f8a8e092830a379ea371962ffe7261f18e7e10))
* **fitting:** pad the fit window, resolve narrow bursts, compare component counts ([e3f060c](https://github.com/DirkKuiper/flits/commit/e3f060c75b033f94112fd0908314c6518ca52a2e))
* improve FLITS adoption and agent workflows ([6c42b9b](https://github.com/DirkKuiper/flits/commit/6c42b9bc76e3fb00fcae0cc086bb42fcb0c23bbc))
* **web:** put the fit's answer and its caveats first in the Fitting tab ([bc20b97](https://github.com/DirkKuiper/flits/commit/bc20b97bfb663abd584fc466b8be25de523cee0f))


### Bug Fixes

* **agents:** check optional fitting capabilities separately ([687e8fd](https://github.com/DirkKuiper/flits/commit/687e8fdf57918cf95463a306ecaf67502b534135))
* **agents:** validate trial fixtures and preserve evaluated provenance ([01878fd](https://github.com/DirkKuiper/flits/commit/01878fd4180c3f528f0303720b456e5502aa3b1b))
* **fitting:** raise automatic budgets and retain capped candidates ([3b32cc6](https://github.com/DirkKuiper/flits/commit/3b32cc61b2481d4ed14ddb13333603119f553e6e))
* **fitting:** use only converged fits as the temporal width cross-check ([7dbcfac](https://github.com/DirkKuiper/flits/commit/7dbcfac1e29e9f6aafab228bb4879c19f8f0ec2c))
* **fitting:** use only converged fits as the temporal width cross-check ([455922c](https://github.com/DirkKuiper/flits/commit/455922c85208a06915786f7c35759fdc657b0e90))
* **packaging:** include reproducible workflows in source distributions ([56774a3](https://github.com/DirkKuiper/flits/commit/56774a3093a3694c85197ef8ee98ee0a9bfbe104))
* **packaging:** retain contributor and citation guidance in source archives ([d174287](https://github.com/DirkKuiper/flits/commit/d1742875cfd8fbcd4decb09b2cea40074d0a7183))
* **web:** show a component comparison made before any model fit ([9b394b1](https://github.com/DirkKuiper/flits/commit/9b394b1319b18a396fea400c8f729a4878805d14))


### Documentation

* **agents:** verify source checkouts and browser runtimes ([b1f985f](https://github.com/DirkKuiper/flits/commit/b1f985f4468bb3a6314ef5afc4783849e2be2210))
* clarify CHIME catalog flux calibration limits ([63d6338](https://github.com/DirkKuiper/flits/commit/63d63380dc78c901af6b465e81cde9b4b6c748bc))
* consolidate validation and add PSRFITS and CHIME examples ([eabf5c3](https://github.com/DirkKuiper/flits/commit/eabf5c31de170f6891c069302396563387f60abf))
* **paper:** describe how other tools reach FLITS's data, not adapters ([ca8b11b](https://github.com/DirkKuiper/flits/commit/ca8b11b433545264e08344eacaa02f2d0d40d97d))
* **paper:** describe how other tools reach FLITS's data, not adapters ([03e9b1c](https://github.com/DirkKuiper/flits/commit/03e9b1cc24ba666fc586ef4d3f23cce18d5a4d0f))
* **paper:** say what the pulsar and FRB 20240209A studies use FLITS for ([a257535](https://github.com/DirkKuiper/flits/commit/a25753539ce486cf0812f7af56fb95eb71f01155))

## [1.6.0](https://github.com/DirkKuiper/flits/compare/flits-v1.5.0...flits-v1.6.0) (2026-10-27)


### Features

* **fitting:** fit the fitburst burst model natively ([0183a58](https://github.com/DirkKuiper/flits/commit/0183a586fc984c618d97380671b2762f643976cb))
* **fitting:** fit the fitburst burst model natively ([4575fb0](https://github.com/DirkKuiper/flits/commit/4575fb01f5df0be1f53ffd99883f6866557f50e8))
* guide coding agents to FLITS with a skill and clearer help ([dd62d8c](https://github.com/DirkKuiper/flits/commit/dd62d8c4a4496b578c3ce2ee385e77290ca1c4c5))
* guide coding agents to FLITS with a skill and clearer help ([6b7f4be](https://github.com/DirkKuiper/flits/commit/6b7f4be1bebdefcb7be041224fa2fc24eac64b63))
* **io:** read SIGPROC and PSRFITS search data without your ([9389ada](https://github.com/DirkKuiper/flits/commit/9389ada0eb2cad4adfecde82c4eac8a811405e6d))
* **io:** read SIGPROC and PSRFITS search data without your ([d0616a4](https://github.com/DirkKuiper/flits/commit/d0616a4053e6d2d451e3649f116ba72fa0857990))
* mask interference without jess ([a00a098](https://github.com/DirkKuiper/flits/commit/a00a0988cd2fdb93628c689db1656537fe795b6b))
* mask interference without jess ([79ac3df](https://github.com/DirkKuiper/flits/commit/79ac3dfaa5c2c956e73352dd9c07c12b7e1530b4))


### Bug Fixes

* **io:** sum two polarizations without wrapping 8-bit samples ([0bad0de](https://github.com/DirkKuiper/flits/commit/0bad0de735e913c9cce38c06296f72de468bea52))
* **web:** keep Run Model Fit enabled after every action ([f2bf858](https://github.com/DirkKuiper/flits/commit/f2bf8586aef4eb0e6d597f65010578ee80885cf5))
* **web:** make browsers revalidate the interface after an upgrade ([5ac8052](https://github.com/DirkKuiper/flits/commit/5ac8052b3f4f837a741f9ec2dc12a510b7e5656b))


### Performance Improvements

* **localization:** localize bursts two to five times faster ([770764f](https://github.com/DirkKuiper/flits/commit/770764f5eb7180001f0502d52670a2f6c486c158))
* **localization:** localize bursts two to five times faster ([b280222](https://github.com/DirkKuiper/flits/commit/b28022221a918f6951e794465286c623a513f7f7))


### Documentation

* cite FLITS by its Zenodo DOI ([c6511b7](https://github.com/DirkKuiper/flits/commit/c6511b75c14c64ea6be8b27e55249dc46686c4da))
* cite FLITS by its Zenodo DOI ([576e423](https://github.com/DirkKuiper/flits/commit/576e423706d8c3fd3540a8dfb951606814adc458))
* **paper:** add C. Makarytchev's fitburst modelling with FLITS ([118827c](https://github.com/DirkKuiper/flits/commit/118827ce4613a20db4532eaef0a192b7f969fcc8))
* **paper:** describe A. Moroianu's use of FLITS accurately ([53a595a](https://github.com/DirkKuiper/flits/commit/53a595a2d551fe117248d76c2be979c60cee9441))
* **paper:** describe FLITS's own burst-model fits ([fe68097](https://github.com/DirkKuiper/flits/commit/fe6809746545d231257c442982d98c999bc39c33))
* **paper:** describe FLITS's own readers and interference masking ([dd0f0a5](https://github.com/DirkKuiper/flits/commit/dd0f0a566995eee33c473461cf384815a0029344))
* **paper:** list Jeff Huang's FRB 20240114A study as a separate paper ([fd150d4](https://github.com/DirkKuiper/flits/commit/fd150d4538838d3b4ffec04a2d26e34eef2a6af9))
* **paper:** name research uses and describe batch measurement for arXiv ([3cb2baa](https://github.com/DirkKuiper/flits/commit/3cb2baa6cbb4bc5248ccf6e0fb9dc593c7c227e5))
* **paper:** name research uses and describe batch measurement for arXiv ([255e248](https://github.com/DirkKuiper/flits/commit/255e248bf1790a96796902521c70900c21ab5438))

## [1.5.0](https://github.com/DirkKuiper/flits/compare/flits-v1.4.0...flits-v1.5.0) (2026-10-23)


### Features

* describe every uncertainty by what it is, and export its value ([b9874f8](https://github.com/DirkKuiper/flits/commit/b9874f86873eb1ffb598365899fd5a88f40075cf))
* **dm:** report a DM range with the smoothed-structure metric ([ae7058f](https://github.com/DirkKuiper/flits/commit/ae7058faf0050d91bb60da02ef84d2542b898d7d))
* report a DM range with the smoothed-structure metric (Sutinjo et al. 2023) ([3e7ef81](https://github.com/DirkKuiper/flits/commit/3e7ef81fab4825dcc464676a9e32ae3cdc1b95f5))
* **uncertainties:** say what each uncertainty is, and always give its value ([7d78823](https://github.com/DirkKuiper/flits/commit/7d78823a4fc9b9e4d22e3f7e2b5160826a472a24))
* **web:** label uncertainties by what they are, in neutral badges ([834088f](https://github.com/DirkKuiper/flits/commit/834088f180c241c18ab984c1dca831bb13cffabf))
* **web:** show the smoothed-structure DM range in the DM panel ([3791c2f](https://github.com/DirkKuiper/flits/commit/3791c2f950c3fe3eb791d837f4e90311ea6d3ca6))


### Documentation

* document the smoothed-structure DM metric and its range ([fb6014a](https://github.com/DirkKuiper/flits/commit/fb6014a1af8dbd0a90207343fb97895dc2e563a0))
* explain the kinds of uncertainty once, in an Uncertainties page ([74c0c91](https://github.com/DirkKuiper/flits/commit/74c0c91e3e04018aa1b495f129e1478db6174865))

## [1.4.0](https://github.com/DirkKuiper/flits/compare/flits-v1.3.0...flits-v1.4.0) (2026-10-23)


### Features

* **cli:** measure bursts without a browser with flits measure ([8b64256](https://github.com/DirkKuiper/flits/commit/8b64256a9a7d652562bdb6839d05c5fda93a81be))
* measure bursts without a browser with flits measure ([2658389](https://github.com/DirkKuiper/flits/commit/2658389efd323e8390a160a5892f183a8b1f36c6))
* open a prepared example burst with flits demo ([8863dba](https://github.com/DirkKuiper/flits/commit/8863dba6e7ec46dd3575533ed782b96decea6590))


### Documentation

* document scripted and batch measurement ([a0e4c80](https://github.com/DirkKuiper/flits/commit/a0e4c8034dca1636cb327e65adb1962451f5a662))
* point the docs home page at flits demo and flits measure ([09ef3c1](https://github.com/DirkKuiper/flits/commit/09ef3c1e9a18627155110a999ccb908310e1c9b1))
* publish llms.txt and add AGENTS.md for coding agents ([b9630ac](https://github.com/DirkKuiper/flits/commit/b9630ac0db51097313d01285f8ac96e443c1c7cf))
* publish llms.txt and add AGENTS.md for coding agents ([95443ea](https://github.com/DirkKuiper/flits/commit/95443ea33072f4efe19e8b8174a51799adefd416))

## [1.3.0](https://github.com/DirkKuiper/flits/compare/flits-v1.2.2...flits-v1.3.0) (2026-10-23)


### Features

* **provenance:** record software origins and verify historical files ([c16348b](https://github.com/DirkKuiper/flits/commit/c16348b7fb5aaefc06968af2a6b02d314fd43138))
* **provenance:** record software origins and verify historical files ([f4ee516](https://github.com/DirkKuiper/flits/commit/f4ee516262f3d7cd42b7be14eeac46aba4141fa5))
* **python:** support Python 3.14 ([c949b00](https://github.com/DirkKuiper/flits/commit/c949b00c0ce5f4cb19fc32bf5a086b4f3ab86c6e))
* **python:** support Python 3.14 ([54c7157](https://github.com/DirkKuiper/flits/commit/54c7157556c90662fe19d2f5b235e12a1d14f32d))


### Documentation

* **paper:** address final comments ([2e233f9](https://github.com/DirkKuiper/flits/commit/2e233f99c14c99e4616802cd2868034c3ff58473))
* **paper:** address final supervisor citation comments ([4723822](https://github.com/DirkKuiper/flits/commit/4723822bbd051137db794e52fbee11324f1a5416))
* **paper:** update and refine AI disclosure statement ([54c1c1a](https://github.com/DirkKuiper/flits/commit/54c1c1aca033b1518cd6dfea73d0ee6f9a40af6c))
* **paper:** update and refine AI disclosure statement ([b7c9fab](https://github.com/DirkKuiper/flits/commit/b7c9fabcbb87fc9307702c909cc397c4ae0ac8a7))

## [1.2.2](https://github.com/DirkKuiper/flits/compare/flits-v1.2.1...flits-v1.2.2) (2026-09-24)


### Documentation

* illustrate FLITS workflows ([8dde637](https://github.com/DirkKuiper/flits/commit/8dde6377125eceedfaa2dcab8d801333f8d51e47))
* improve FLITS workflow guides and JOSS manuscript ([1a04b01](https://github.com/DirkKuiper/flits/commit/1a04b019147b9c5c09282f55c345e399addcfc8c))
* **paper:** clarify scientific motivation and incorporate coauthor feedback ([ac2d955](https://github.com/DirkKuiper/flits/commit/ac2d955707800b5bf8fd41ce280b5f4f982b2133))

## [1.2.1](https://github.com/DirkKuiper/flits/compare/flits-v1.2.0...flits-v1.2.1) (2026-09-21)


### Documentation

* **joss:** prepare manuscript and reproducible submission materials ([f31ae6b](https://github.com/DirkKuiper/flits/commit/f31ae6be232771dcd270433f3f6720f9404a93d3))
* **paper:** streamline impact and AI disclosure ([e5ecf32](https://github.com/DirkKuiper/flits/commit/e5ecf32cd8e7d772f757a12efd8b89141c804d0c))

## [1.2.0](https://github.com/DirkKuiper/flits/compare/flits-v1.1.0...flits-v1.2.0) (2026-08-25)


### Features

* **drift:** measure sub-burst drift rate ([a1f78f5](https://github.com/DirkKuiper/flits/commit/a1f78f506ec2805bb8a3e12aeced77fe1b92d835))
* **drift:** measure the sub-burst drift rate ([9d939bf](https://github.com/DirkKuiper/flits/commit/9d939bf4e374222e6fc146af675e95699aca7512))
* **exports:** carry the drift rate through every export artifact ([64b22e1](https://github.com/DirkKuiper/flits/commit/64b22e1cc9ee091de347e7ad137b1e6d49f18d50))
* **web:** add the Drift tab and replay the drift rate headlessly ([1bc865f](https://github.com/DirkKuiper/flits/commit/1bc865f86e856867dfb70c4a09c2ab93665c236c))


### Bug Fixes

* **drift:** address the review of the drift measurement ([2d9c455](https://github.com/DirkKuiper/flits/commit/2d9c4557807f1ca9b9c862d3f3c46b31d29f6041))
* **drift:** derive the DM degeneracy from the slope, not the drift rate ([cb0b1af](https://github.com/DirkKuiper/flits/commit/cb0b1afce2bb6aca7296610b203d8a8144afe063))
* **drift:** harden analysis validation ([1b6a55e](https://github.com/DirkKuiper/flits/commit/1b6a55ea198d45c30738b65348a7454bd5b101eb))
* **drift:** refuse irregular axes and judge significance against the real bar ([a9396e2](https://github.com/DirkKuiper/flits/commit/a9396e2cb992d43aa22e0e6dd344832985e3974b))

## [1.1.0](https://github.com/DirkKuiper/flits/compare/flits-v1.0.1...flits-v1.1.0) (2026-08-25)


### Features

* full-Stokes input and in-session polarimetry ([a2434b5](https://github.com/DirkKuiper/flits/commit/a2434b5366d6d3b05812509c21c076556e0093c1))
* **io:** read full Stokes from files that carry four polarizations ([6de3425](https://github.com/DirkKuiper/flits/commit/6de342532465434ef07faf88eb040787a2d810c3)), closes [#92](https://github.com/DirkKuiper/flits/issues/92)
* **session:** measure the rotation measure from the session's own selections ([a96c781](https://github.com/DirkKuiper/flits/commit/a96c78142531c6f7520c01a72f328b9b3be89a7a)), closes [#92](https://github.com/DirkKuiper/flits/issues/92)


### Bug Fixes

* **web:** let the last polarization run own the panel ([34ebc5b](https://github.com/DirkKuiper/flits/commit/34ebc5b0d79214ac3f99e35de12f648bced91491))


### Documentation

* describe full-Stokes input and in-session polarimetry ([af5b2c3](https://github.com/DirkKuiper/flits/commit/af5b2c3eb6dcda665c7d8c67d82381c06eafbf4b))

## [1.0.1](https://github.com/DirkKuiper/flits/compare/flits-v1.0.0...flits-v1.0.1) (2026-08-24)


### Documentation

* **joss:** prepare publication materials ([e3f9b35](https://github.com/DirkKuiper/flits/commit/e3f9b35026192abbc20aefa3500a6dd9c32bf719))
* **joss:** prepare publication materials ([#96](https://github.com/DirkKuiper/flits/issues/96)) ([680ffde](https://github.com/DirkKuiper/flits/commit/680ffde47791a11b8b577c4b0926f313bd78d151))

## [1.0.0](https://github.com/DirkKuiper/flits/compare/flits-v0.8.0...flits-v1.0.0) (2026-08-24)


### ⚠ BREAKING CHANGES

* the data directory is now a containment boundary. Paths resolving outside --data-dir are rejected with 403; pass --allow-outside-data-dir to restore the previous behaviour. Cross-origin browser requests are no longer accepted from any origin by default; name them with --cors-origin.

### Features

* **analysis:** add selection-aware temporal metrics ([adc8c4b](https://github.com/DirkKuiper/flits/commit/adc8c4b0bf7b19b66e0baf64f1a5d71a4606d2d9))
* **cli:** add headless session replay ([ad8b2dd](https://github.com/DirkKuiper/flits/commit/ad8b2dd75de478537fd6c27f688364680a94c467))
* JOSS v1.0 readiness ([#91](https://github.com/DirkKuiper/flits/issues/91)) ([f3d95d4](https://github.com/DirkKuiper/flits/commit/f3d95d48cda7797d7bbb513de3e59780259bbcc8))
* **polarization:** complete RM synthesis workflow ([823bbd1](https://github.com/DirkKuiper/flits/commit/823bbd15aec25c1626251f7c9b50185d7ab39f23))
* **web:** log failures and bound the session store ([c0ab87c](https://github.com/DirkKuiper/flits/commit/c0ab87cecab5393405632c09a3a9e558c7c0df1a))


### Bug Fixes

* **analysis:** stop dedispersion wrapping and compounding rounding error ([d531213](https://github.com/DirkKuiper/flits/commit/d5312138bd4432acd6eff5a1eebae5584c0c224e))
* **ci:** accept pip's vendored advisories with a written justification ([6e1c1ae](https://github.com/DirkKuiper/flits/commit/6e1c1ae150b00fa8699c4f5070bb69874b01c294))
* **ci:** make browser tests opt-in and unpin the mypy target ([ae85998](https://github.com/DirkKuiper/flits/commit/ae8599822187e1a530a4fc9c53ddea89b9155d4b))
* **deps:** declare compatible ranges instead of exact pins ([6477338](https://github.com/DirkKuiper/flits/commit/6477338536ba458ed6b74dfe9f9cc929f3ed3d91))
* **docker:** apply the security floors last and verify them ([55ffa64](https://github.com/DirkKuiper/flits/commit/55ffa6426a6eff5e359196902f68f1fa796c72c4))
* **docker:** clear the container vulnerability scan ([b55e0bb](https://github.com/DirkKuiper/flits/commit/b55e0bbd0344be66cfc5768a390956dfcd3b9d2e))
* **io:** treat NRT IQUV pol headers as AA/BB coherency ([#88](https://github.com/DirkKuiper/flits/issues/88)) ([ddaab63](https://github.com/DirkKuiper/flits/commit/ddaab6339f651ec27efde1febc143482e18950e4))
* **web:** contain file access within the configured data directory ([1ec0206](https://github.com/DirkKuiper/flits/commit/1ec0206c0c6f350527a5fc762de317525c42fcdb))


### Documentation

* add contributing, conduct, and security guidelines ([0f90378](https://github.com/DirkKuiper/flits/commit/0f90378c04b6223b5f040b952d9d3d2893c9c0bb))
* add JOSS paper and a statement of need ([ff91a9e](https://github.com/DirkKuiper/flits/commit/ff91a9ebf733135af9b06657f74ddfe4b1affd92))
* deepen the analysis pages and state the polarization scope ([7f94e80](https://github.com/DirkKuiper/flits/commit/7f94e80af6ea4792ff4247a342ea4f310b19834a))
* document the Python API and attribute the methods ([6e74ab2](https://github.com/DirkKuiper/flits/commit/6e74ab28bdbea3f3784e05a83afad4e068b98652))
* move the import to the top of the api reference example ([f12a7cf](https://github.com/DirkKuiper/flits/commit/f12a7cf5f9721e80075be6f97c9d10afbf050275))

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
