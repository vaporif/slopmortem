# Premortem report for (unnamed)

Pitch: We're building a US consumer crypto savings platform that pays high yield on customer deposits by lending them to institutional borrowers and market makers. Web + mobile, no account fees, tiered interest rates by lock-up duration. Already custodying ~$50M of customer assets in stablecoins and BTC.

Generated: 2026-04-30T19:52:47.371540+00:00

> Two known display issues at the time this run was captured, both since fixed (re-ingest required to take effect for the Sources case):
> - `Sources:` blocks rendered empty because ingest stored synthetic ids (`curated:Celsius Network`) in `payload.sources` instead of URLs, so the synth-stage host allowlist filter dropped every citation the LLM returned.
> - `cost_usd_total` printed `0.00` because the OpenRouter client never settled per-call cost into the shared `Budget`, and the report footer formatted with `.2f` (truncating sub-cent runs).

## Celsius Network

Crypto yield-savings platform that paid retail depositors high interest by lending customer assets to institutional borrowers; collapsed in June 2022 after a liquidity crisis triggered a bank-run, leading to Chapter 11 bankruptcy in July 2022.

Failure date: 2022-07-13
Lifespan: 61 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.5 | The new pitch is structurally identical to Celsius: accept crypto deposits (stablecoins and BTC) from retail consumers, pay tiered yield, fund that yield by on-lending assets to institutional borrowers and market makers, no account fees, web + mobile delivery. Celsius operated the same deposit-lend-yield loop, also charged no user fees, and also offered tiered rates. The match is near-perfect. |
| market | 9.0 | Both target US retail consumers seeking above-market yield on crypto holdings, specifically stablecoins and BTC. Celsius explicitly described its audience as people who wanted 'a better savings account' in crypto; the new pitch uses identical framing. The institutional borrower and market-maker demand side is also the same. |
| gtm | 8.0 | Celsius launched via a mobile app and web platform with no fees, relying on word-of-mouth and the high-yield offer as the primary growth lever — precisely what the new pitch describes. Celsius also used its own CEL token as a yield-booster (no such token mentioned in the new pitch, which is a small divergence), and later invested heavily in brand marketing. |
| stage_scale | 6.5 | Celsius at its peak had ~$12B AUM and 1.7M customers; the new pitch reports ~$50M AUM, suggesting very early traction. Both are/were pre-profitability, deposit-funded, and operating without bank charter. The new pitch is much earlier stage than Celsius at collapse, but the structural risk profile is the same. |

Why similar:

The new pitch replicates the Celsius business model almost verbatim: retail crypto deposits (stablecoins + BTC), high yield funded by institutional lending, tiered lock-up rates, no fees, web + mobile. Celsius operated exactly this stack and reached ~$12B AUM before collapsing. The fundamental economic tension — promising liquid-ish high yield while deploying assets into less-liquid institutional loans — is structurally identical.

Where diverged:

1. Asset scope: the new pitch explicitly focuses on stablecoins and BTC only, whereas Celsius supported a broad range of cryptocurrencies including ETH and its own CEL token — narrower asset scope reduces but does not eliminate duration-mismatch risk. 2. No proprietary token: the new pitch does not mention issuing a native token (Celsius used CEL to pay and manipulate yields), removing the Ponzi-amplifier and market-manipulation vector that accelerated Celsius's collapse. 3. Scale: the new pitch is at ~$50M AUM vs. Celsius's ~$12B at peak — far earlier stage, which gives more time to address structural issues before becoming systemically significant. 4. Lock-up framing: the new pitch explicitly uses tiered lock-up durations, suggesting some attempt to match asset-liability duration; Celsius offered effectively demand-liquid accounts which exacerbated the bank-run dynamic.

Failure causes:

- excessive asset re-hypothecation and leverage
- duration mismatch between liquid deposits and illiquid institutional loans
- catastrophic liquidity crisis triggered by Terra/Luna market shock
- absence of deposit insurance or regulatory backstop
- unregistered securities offering attracting multi-state cease-and-desist orders
- CEO fraud, market manipulation of CEL token, and insider self-dealing
- misleading public statements concealing insolvency

Lessons:

- Maintain a disclosed, audited liquidity reserve sufficient to cover a bank-run scenario — Celsius had no such buffer and failed within days of a confidence shock.
- Seek proactive regulatory clarity on whether tiered yield accounts constitute unregistered securities before scaling; Celsius ignored state cease-and-desist orders and paid a $4.7B FTC settlement.
- Never re-hypothecate customer assets beyond a conservative, publicly-disclosed LTV ceiling; Celsius's 'endless re-hypothecation' was flagged by its own custodian as destined for failure.
- Implement strict asset-liability duration matching: lock-up tiers must align with the actual tenor of institutional loans, not just serve as a marketing feature.
- Publish regular, third-party-verified proof-of-reserves so depositors can independently verify solvency — Celsius's opacity accelerated the panic once rumors began.

Sources:



## BlockFi

US consumer crypto savings and lending platform that paid yield on deposits by lending to institutional counterparties, filed Chapter 11 in November 2022 following FTX's collapse.

Failure date: 2022-11-28
Lifespan: 70 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.5 | The pitch and BlockFi share an almost identical business model: accept consumer crypto deposits (stablecoins and BTC), pay tiered yield, and fund that yield by lending to institutional borrowers and market makers. Both monetise the spread between retail deposit rates and institutional lending rates. |
| market | 9.0 | Both target US retail consumers seeking crypto-native yield — a market BlockFi pioneered. The pitch is entering the same regulatory environment (SEC/state securities scrutiny) and the same post-FTX trust-deficit landscape that killed BlockFi. |
| gtm | 7.5 | Both use web and mobile direct-to-consumer channels with no account fees as the primary acquisition hook. BlockFi also relied heavily on brand trust and influencer marketing; the pitch does not specify GTM beyond product channels, so the overlap is product-led but the acquisition strategy is underspecified. |
| stage_scale | 6.5 | BlockFi at a comparable early stage had a similar AUM footprint; ~$50 M in custody for the pitch versus BlockFi's early growth phase before reaching billions. Both are pre-scale but post-product with real customer assets, making risk concentration already material. |

Why similar:

The pitch is structurally near-identical to BlockFi: retail-facing, yield-bearing crypto accounts funded by institutional lending, covering the same asset classes (stablecoins, BTC), the same fee model (no account fees, tiered rates by lock-up), and the same US consumer market. BlockFi reached a $3 B valuation with this exact model before its bankruptcy proved the model's fatal dependency on counterparty solvency.

Where diverged:

The pitch does not disclose its institutional borrower set or counterparty concentration — this is the single most critical divergence dimension. BlockFi's death was caused by concentrated exposure to FTX/Alameda. If the new pitch has deliberately diversified its counterparty book, enforces over-collateralisation, or uses on-chain transparent collateral rather than opaque OTC loans, that would be a meaningful structural divergence from BlockFi. However, the pitch does not evidence any of these controls, so the divergence is asserted but unproven. The pitch also does not mention the $100 M SEC/state regulatory settlement BlockFi faced for unregistered securities offerings — it is unclear whether the new platform has pre-cleared its yield product with regulators or is repeating BlockFi's compliance path.

Failure causes:

- concentrated counterparty exposure to FTX/Alameda
- contagion from FTX bankruptcy triggering withdrawal halt
- SEC and state regulatory action ($100 M settlement for unregistered securities)
- $227 M in uninsured funds at Silicon Valley Bank
- insufficient liquidity buffers to meet sudden redemption demands
- dependence on a single rescue facility (FTX $400 M credit line) from an ultimately insolvent counterparty
- loss of customer trust following withdrawal suspension

Lessons:

- Enforce strict counterparty concentration limits: cap exposure to any single institutional borrower well below total AUM and require over-collateralisation with on-chain verifiable collateral.
- Engage securities regulators before launch, not after: BlockFi paid $100 M to settle charges for offering unregistered securities — pre-clear your yield product structure with the SEC and relevant state regulators.
- Maintain a segregated liquidity reserve large enough to cover a correlated run on withdrawals; do not deploy 100% of deposits into illiquid institutional loans.
- Avoid rescue financing from entities that are also counterparties or competitors in the same ecosystem — BlockFi's FTX credit facility created a fatal single point of failure.
- Diversify custodial and banking relationships and keep fiat reserves at FDIC-insured institutions to avoid the additional $227 M loss BlockFi suffered at Silicon Valley Bank.

Sources:



## Voyager Digital

US crypto brokerage and yield-bearing lending platform that custodied consumer deposits and lent them to institutional borrowers, collapsing after Three Arrows Capital defaulted on $666M in loans.

Failure date: 2022-07-05
Lifespan: 49 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.0 | Both platforms custodied consumer crypto assets (stablecoins and BTC) and generated yield by lending those deposits to institutional borrowers and market makers — an almost identical liability-funding-asset structure with the consumer bearing the credit risk of the institutional counterparty. |
| market | 9.0 | Both target US retail consumers seeking high-yield crypto savings products; Voyager explicitly offered loyalty/yield rewards on customer deposits in the same domestic regulatory environment. |
| gtm | 7.0 | Both use web + mobile channels with no account fees and tiered interest/reward rates to attract retail depositors. Voyager also offered brokerage trading, which the new pitch does not explicitly mention, creating a modest divergence in product scope. |
| stage_scale | 6.0 | The new pitch is already custodying ~$50M of customer assets, which mirrors Voyager's early custodial scale before it grew to over $1.3B. Both are/were real-money, live platforms rather than pre-revenue prototypes, but Voyager was significantly larger and publicly listed at the time of failure. |

Why similar:

The new pitch replicates Voyager Digital's core playbook almost exactly: accept consumer crypto deposits (stablecoins and BTC), pay tiered high-yield interest, and fund that yield by on-lending to institutional borrowers and market makers. Voyager operated this same model in the US, on web and mobile, with no account fees. The structural credit risk is identical — if even one large institutional borrower fails to repay, the platform cannot honor consumer withdrawals. Voyager's $1.3B+ in consumer assets locked in bankruptcy directly parallels the risk profile of the new pitch's current $50M AUC.

Where diverged:

1. Lock-up tiers: The new pitch explicitly offers tiered interest rates by lock-up duration, giving consumers a contractual heads-up about liquidity windows; Voyager's product terms were less transparent about liquidity risk. 2. Brokerage vs. pure savings: Voyager combined custody with active crypto trading/brokerage; the new pitch is purely a savings/yield product with no brokerage functionality mentioned. 3. Scale at entry: The new pitch is at ~$50M AUC versus Voyager's >$1B at failure, meaning a single large counterparty default would be relatively more survivable in absolute dollar terms — though proportionally the risk structure is the same. 4. Post-collapse regulatory environment: The new pitch operates after the Voyager/FTX/Celsius collapses prompted heightened SEC, FDIC, and Fed scrutiny, meaning the regulatory bar is materially higher than when Voyager launched in 2018.

Failure causes:

- concentrated institutional counterparty credit risk
- single large borrower default (Three Arrows Capital $666M)
- no adequate collateral or loan loss reserves
- misleading consumer communications about deposit insurance (FDIC cease-and-desist)
- illiquid loan book preventing consumer withdrawals
- cascade contagion from broader 2022 crypto credit market collapse
- regulatory non-compliance and false advertising

Lessons:

- Enforce strict per-borrower exposure limits — no single institutional counterparty should represent more than a small fraction of total consumer deposits, as Three Arrows Capital's single default destroyed Voyager.
- Require over-collateralization and real-time margin monitoring on every institutional loan; unsecured or under-collateralized lending to crypto counterparties is the direct mechanism that killed Voyager.
- Never imply or state that consumer deposits are FDIC-insured or bank-equivalent; the Fed and FDIC forced Voyager to cease and desist for exactly this, adding regulatory liability on top of insolvency.
- Maintain a liquid reserve buffer sufficient to honor redemptions even in a stress scenario; lock-up tiers are only protective if the platform can actually enforce them contractually and disclose the risk clearly to consumers.
- Pre-engage with SEC, state money-transmitter regulators, and banking supervisors before scaling custody assets further — Voyager's post-bankruptcy regulatory objections from the SEC delayed and complicated every recovery option.

Sources:



## Bitconnect

Crypto lending platform that promised high daily interest on locked-up Bitcoin deposits, later found to be a $2.4B Ponzi scheme.

Failure date: 2018-01-17
Lifespan: 24 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 6.5 | Both platforms accept consumer crypto deposits, lock them up for a set duration, and pay tiered interest. The structural mechanic—take custody, promise yield, return principal—is nearly identical at the surface. The critical distinction is that Bitconnect's yield came from an opaque 'trading bot' with no verifiable institutional lending counterparty, whereas the new pitch explicitly names institutional borrowers and market makers as the yield source. Still, the surface-level similarity is strong enough to attract the same regulatory scrutiny. |
| market | 7.5 | Both target US retail consumers seeking high yield on crypto holdings, operating in the same regulatory grey zone of crypto-denominated savings products. The macro backdrop (crypto bull cycle, demand for yield) is structurally similar. Bitconnect operated 2016-2018; the new pitch faces analogous post-FTX regulatory pressure in the current cycle. |
| gtm | 3.5 | Bitconnect relied heavily on multilevel marketing, promoter networks, and viral hype (affiliate referral commissions). The new pitch describes a web+mobile product with no account fees and tiered rates—a direct-to-consumer fintech GTM with no MLM structure mentioned. This is a meaningful divergence. |
| stage_scale | 5.0 | Bitconnect grew to billions in deposits before collapse. The new pitch is at ~$50M AUC, an earlier stage. Both had/have real customer assets at risk. Bitconnect's scale made its collapse catastrophic; the new pitch is smaller but not trivially so. |

Why similar:

Both are US-facing consumer crypto savings platforms that (1) accept crypto deposits (BTC and stablecoins), (2) lock funds for a set duration, (3) pay tiered interest, and (4) custody substantial customer assets. The core value proposition—earn high yield on crypto by lending it out—is functionally identical to Bitconnect's public-facing pitch. Regulators shut Bitconnect down specifically because it sold unregistered securities (yield-bearing lending contracts) to retail investors, the exact product the new pitch describes.

Where diverged:

1. Yield sourcing transparency: The new pitch names institutional borrowers and market makers as explicit counterparties, whereas Bitconnect cited an unaudited proprietary 'trading bot'—a concrete difference in disclosed mechanism. 2. Token dependency: Bitconnect's yield and liquidity were circular, dependent on BCC coin appreciation; the new pitch operates in stablecoins and BTC with no proprietary token, removing the reflexive collapse risk. 3. Go-to-market: No MLM or affiliate promoter structure is described; the new pitch is a direct fintech product. 4. Regulatory posture: Bitconnect operated with no securities registration; the new pitch does not disclose its regulatory status, but the absence of an explicit MLM layer reduces one red flag.

Failure causes:

- regulatory cease-and-desist (unregistered securities sales)
- Ponzi/circular yield mechanics with no real institutional counterparty
- opaque 'trading bot' yield source with no auditability
- multilevel marketing and promoter network driving unsustainable growth
- proprietary BCC token liquidity collapse triggering 92% value crash
- criminal fraud by founders (money laundering, wire fraud)
- complete loss of investor trust upon shutdown

Lessons:

- Register yield-bearing deposit products as securities or obtain explicit regulatory clarity before scaling—Bitconnect was killed by cease-and-desist orders, not market forces.
- Publish audited proof of institutional lending counterparties and their creditworthiness; opacity around yield sourcing is the single fastest way to attract SEC/DOJ scrutiny.
- Maintain a custody and liquidity structure that allows full withdrawal at any time; lock-up mechanics plus opaque yield is the regulatory pattern-match regulators use to identify Ponzi schemes.
- Avoid any affiliate or referral compensation tied to deposits—Bitconnect's MLM structure was cited as a primary indicator of fraud; even a clean product can be tarred by this association.
- Establish clear segregation of customer assets from operational funds and obtain third-party attestations; Bitconnect's ambiguous corporate existence made asset recovery impossible after shutdown.

Sources:



## FTX

Crypto exchange and hedge fund that fraudulently lent $10B of customer deposits to affiliated trading firm Alameda Research, triggering a bank run and Chapter 11 bankruptcy in November 2022.

Failure date: 2022-11-11
Lifespan: 42 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 7.0 | Both businesses custody customer crypto assets and generate yield/returns by lending those assets to institutional counterparties (market makers, trading firms). The new pitch explicitly describes lending customer deposits to institutional borrowers—structurally analogous to how FTX funneled customer funds to Alameda Research as a de-facto lending arrangement, albeit FTX's was undisclosed and fraudulent. |
| market | 8.0 | Both operate in the US consumer crypto market, targeting retail depositors who want yield or returns on stablecoins and BTC. FTX.US was specifically the US-resident retail product and the broader FTX pitch was 'a safe, easy way to get into crypto'—nearly identical positioning to a consumer crypto savings platform. |
| gtm | 5.0 | FTX used massive sports sponsorships, celebrity endorsements, and exchange-first distribution. The new pitch is a direct savings/yield product on web and mobile with tiered interest rates, which is more akin to a neobank GTM (organic, product-led). Both target retail consumers but the acquisition motions differ significantly. |
| stage_scale | 4.0 | FTX at failure had $10B+ active trading volume, 1M+ users, and a $32B valuation. The new pitch is early-stage custodying ~$50M in assets. Scale is orders of magnitude apart, though both have crossed an initial AUM threshold sufficient to attract meaningful counterparty risk. |

Why similar:

Both are US-facing consumer crypto platforms that pool retail customer deposits (stablecoins and BTC) and deploy those funds to institutional counterparties to generate returns. The core promise—earn yield on your crypto—and the operational model—custody assets, lend to institutional borrowers/market makers—are structurally identical. The risk vector is also shared: a mismatch between liquid customer withdrawal obligations and illiquid or impaired loan books can cause a bank-run-style collapse.

Where diverged:

1) Business legality and intent: FTX's lending to Alameda was undisclosed, fraudulent, and concealed via custom software; the new pitch explicitly discloses the lending model to customers upfront, which is a fundamental governance divergence. 2) Product category: FTX was primarily a derivatives/spot trading exchange that layered on yield products; the new pitch is a pure savings/yield platform with no trading venue, reducing exchange-specific risks (e.g., proprietary exchange token as collateral). 3) Scale and regulatory exposure: at ~$50M AUM the new startup is early-stage, while FTX collapsed at $32B valuation—regulatory and systemic-contagion risk profiles differ substantially. 4) Jurisdictional domicile: FTX was incorporated in Antigua & Barbuda and headquartered in the Bahamas to minimize US regulatory reach; the new pitch is explicitly a US consumer platform, meaning it must operate within US regulatory frameworks (MSB licensing, potential SEC/CFTC oversight, state money-transmitter licenses) from day one.

Failure causes:

- undisclosed commingling of customer funds with affiliated trading firm
- fraudulent concealment of $10B inter-company loan via custom software
- complete absence of corporate governance and financial controls
- circular collateral loop (FTT token backing Alameda balance sheet backing FTX)
- bank-run liquidity crisis triggered by public disclosure of balance sheet composition
- founder fraud and criminal self-dealing
- offshore domicile enabling regulatory evasion until collapse

Lessons:

- Disclose the full lending arrangement—counterparty identities, collateral requirements, and concentration limits—in customer-facing terms of service and regular attestations, to prevent any allegation of misrepresentation.
- Establish a third-party custodian or proof-of-reserves audit cadence from day one; customers must be able to verify that their assets are segregated and not rehypothecated beyond disclosed limits.
- Obtain all required US licenses (money transmitter, potentially SEC/CFTC registration) before scaling AUM; operating as an unlicensed entity invites the same regulatory enforcement that hit FTX.US.
- Cap concentration risk in any single institutional borrower and require overcollateralization; FTX's undoing was that a single affiliated counterparty (Alameda) absorbed the entire loan book.
- Build a matched-maturity or liquid-buffer structure so withdrawal demand can be met without a fire sale; tiered lock-up rates alone do not protect against a coordinated bank run if the underlying loans are illiquid.

Sources:



---

Pipeline meta:

- cost_usd_total: 0.0047
- latency_ms_total: 187329
- trace_id: 3a02bfde-2911-7c7b-5de7-02e7c0bc5e8e
- budget_remaining_usd: 2.00
- budget_exceeded: False
- K_retrieve: 30
- N_synthesize: 5

Models:

- facet: anthropic/claude-haiku-4.5
- rerank: anthropic/claude-sonnet-4.6
- synthesize: anthropic/claude-sonnet-4.6
