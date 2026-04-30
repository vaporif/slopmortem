# Premortem report for (unnamed)

Pitch: We're building a US consumer crypto savings platform that pays high yield on customer deposits by lending them to institutional borrowers and market makers. Web + mobile, no account fees, tiered interest rates by lock-up duration. Already custodying ~$50M of customer assets in stablecoins and BTC.

Generated: 2026-04-30T20:24:55.356786+00:00

> Known display issue at the time this run was captured, since fixed (re-ingest required to take effect): `Sources:` blocks rendered empty because ingest stored synthetic ids (`curated:Celsius Network`) in `payload.sources` instead of URLs, so the synth-stage host allowlist filter dropped every citation the LLM returned.

## Celsius Network

Crypto yield platform paying high interest on customer deposits by lending to institutional borrowers and market makers — collapsed in June 2022 after a bank run exposed a $1.2B balance sheet deficit.

Failure date: 2022-07-13
Lifespan: 61 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.5 | The new pitch is nearly identical to Celsius's core model: accept consumer crypto deposits (stablecoins and BTC), pay tiered interest rates, fund yields by lending to institutional borrowers and market makers, charge no account fees, and offer web + mobile access. Celsius did exactly this and called its product a 'better savings account.' |
| market | 9.0 | Both target US retail consumers seeking high yield on crypto holdings, with institutional borrowers on the other side. Celsius had 1.7 million US customers and ~$12B AUM at peak; the new pitch already has ~$50M in custody, signaling the same demand pool and competitive landscape. |
| gtm | 8.0 | Both rely on the appeal of high yield (Celsius advertised up to 17% APY) versus traditional bank rates to acquire retail depositors. The new pitch uses tiered lock-up durations as a differentiation lever, whereas Celsius used its CEL token for bonus yield — a modest GTM difference but the same fundamental acquisition hook. |
| stage_scale | 6.5 | Celsius at its comparable early stage (~$50M AUM in 2018-2019) mirrors the new pitch's current $50M in custody. However, Celsius had already done a $50M ICO by that point and was pursuing rapid global expansion, while the new pitch appears to be a leaner, domestically focused build — a meaningful but not dramatic stage divergence. |

Why similar:

The new pitch is structurally a near-replica of Celsius Network's business: US consumer-facing crypto savings product, deposits in stablecoins and BTC, high yield funded by institutional lending, tiered rates, no fees, web and mobile delivery. Even the asset base (~$50M) mirrors Celsius's early-stage footprint. The core risk — a maturity/liquidity mismatch between immediate-withdrawal consumer deposits and less-liquid institutional loans — is identical.

Where diverged:

1. Lock-up tiers vs. liquid accounts: The new pitch explicitly uses tiered interest rates by lock-up duration, which partially mitigates the instantaneous bank-run risk that destroyed Celsius (whose terms allowed immediate withdrawal requests against illiquid loan books). 2. No proprietary token: The pitch does not mention a CEL-like native token, removing one major source of manipulation risk and regulatory scrutiny Celsius faced. 3. Scope: The pitch is US-only and consumer-focused without the global multi-office expansion Celsius pursued, suggesting lower operational burn. 4. Scale discipline: At $50M AUM the pitch is far smaller and presumably has not yet engaged in the aggressive re-hypothecation that Prime Trust flagged at Celsius. Whether these differences are durable safeguards or merely current-stage omissions is the critical unknown.

Failure causes:

- excessive re-hypothecation of customer assets to juice yields
- liquidity mismatch between demand-deposit liabilities and illiquid institutional loans
- bank run triggered by broader crypto market collapse (Terra/Luna contagion)
- regulatory non-compliance — unregistered securities offering in multiple US states
- insider fraud and market manipulation of CEL token by executives
- Ponzi-like yield structure unsustainable without continuous new deposit inflows
- inadequate risk controls and custodian warnings ignored

Lessons:

- Implement strict liquidity reserves and match lock-up durations on the liability side to loan tenor on the asset side — never promise liquidity you cannot fund.
- Engage US securities regulators proactively before scaling; interest-bearing crypto accounts are likely unregistered securities and operating without clarity invites cease-and-desist orders that can collapse customer confidence overnight.
- Avoid proprietary tokens or any mechanism that creates circular yield dependencies; these obscure true solvency and attract fraud charges.
- Publish audited proof-of-reserves and transparent loan-book disclosures to depositors; opacity was the proximate trigger of the Celsius bank run even before the balance sheet was impaired.
- Do not re-hypothecate customer assets beyond a conservative, disclosed LTV — your custodian or prime broker will terminate the relationship when leverage becomes extreme, as Prime Trust did with Celsius in June 2021.

Sources:



## BlockFi

Crypto interest-bearing accounts funded by lending customer deposits to institutional borrowers, ultimately bankrupted by concentrated counterparty exposure to FTX and Three Arrows Capital.

Failure date: 2022-11-28
Lifespan: 65 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.5 | The new pitch is a near-identical business model: accept consumer crypto deposits (stablecoins and BTC), pay high yield, fund that yield by lending to institutional borrowers and market makers — exactly BlockFi's core product loop. Tiered interest by lock-up mirrors BlockFi's interest-account tiers. |
| market | 9.0 | Both target US retail consumers seeking yield on crypto holdings. BlockFi's regulatory battles with US state securities regulators and the SEC are directly relevant to any US-based crypto savings product. |
| gtm | 7.5 | Both rely on web and mobile distribution to retail depositors with no account fees as the key acquisition hook. BlockFi also used high advertised APY as its main GTM lever, as does the new pitch. Minor divergence possible in partnership or referral strategies not disclosed in the pitch. |
| stage_scale | 7.0 | BlockFi had already custodied billions at comparable growth stages. The new pitch at ~$50M AUC is earlier, but the shape of growth — consumer AUC as the north-star metric with yield competitiveness as the driver — is the same. |

Why similar:

The new pitch replicates BlockFi's exact value proposition: a US consumer crypto savings platform paying high yield by intermediating deposits to institutional borrowers. Both products custody stablecoins and BTC, charge no account fees, and differentiate on yield rate. The structural risk profile is also identical — yield sustainability depends on the creditworthiness of institutional counterparties, and a withdrawal run can be triggered instantly once confidence breaks. BlockFi's $100M SEC/state settlement over unregistered securities further signals that this product type sits in a high-regulatory-scrutiny zone.

Where diverged:

1. Scale: the new pitch is at ~$50M AUC vs. BlockFi's multi-billion peak, meaning counterparty concentration risk per dollar is higher but total systemic exposure is lower. 2. Timing & regulatory environment: post-BlockFi/Celsius/Voyager collapses, US regulators have dramatically tightened enforcement posture; the new pitch must navigate a materially harder legal landscape. 3. Lock-up tiers: the new pitch explicitly structures tiered rates by lock-up duration, which BlockFi did not prominently feature in its core BIA product — this could reduce instantaneous withdrawal liquidity risk if enforced contractually. 4. Counterparty disclosure: the pitch does not yet specify who the institutional borrowers are, whereas BlockFi's fatal flaw was undisclosed concentration in Alameda/FTX and Three Arrows Capital.

Failure causes:

- concentrated counterparty exposure to FTX/Alameda
- contagion from FTX bankruptcy triggering withdrawal halt
- $100M regulatory settlement for unregistered securities offering
- secondary exposure to Three Arrows Capital default
- $227M uninsured funds at Silicon Valley Bank
- insufficient liquidity buffers against simultaneous borrower default and depositor run
- over-reliance on single rescue counterparty (FTX credit facility)

Lessons:

- Diversify institutional borrower exposure aggressively and publish counterparty concentration limits to depositors — opacity was BlockFi's reputational and legal death sentence.
- Obtain clear regulatory guidance (or register the product) before scaling AUC; BlockFi's $100M settlement and subsequent lending-account freeze destroyed user trust and operational headroom.
- Maintain a dedicated liquid reserve or insurance fund sized to cover a simultaneous multi-borrower default scenario, not just normal redemptions.
- Structure and legally enforce lock-up periods so that a confidence shock cannot instantly drain all deposits — contractual lock-ups are only protective if they hold under stress.
- Avoid single-entity rescue deals (like BlockFi's FTX credit facility) that create existential dependency on one counterparty; when that counterparty fails, you fail with it.

Sources:



## Voyager Digital

Cryptocurrency brokerage and lending platform that paid yield on customer deposits by lending to institutional borrowers, collapsed after a $666M loan default by Three Arrows Capital.

Failure date: 2022-07-05
Lifespan: 49 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.0 | Both platforms custody customer crypto assets (stablecoins and BTC), pay yield to retail depositors, and generate that yield by on-lending to institutional borrowers and market makers — an almost identical intermediary lending model with the same structural mismatch between liquid customer liabilities and illiquid institutional loans. |
| market | 9.0 | Both target US retail consumers seeking high-yield crypto savings, operating in the same regulatory environment (SEC, FDIC, Federal Reserve scrutiny) and the same macro crypto cycle. |
| gtm | 7.0 | Both use web and mobile apps with tiered/interest-bearing accounts and no account fees as the primary acquisition hook. Voyager also operated as a brokerage, adding trading as a stickiness layer, which the new pitch does not explicitly mention. |
| stage_scale | 8.0 | The new pitch reports ~$50M in customer assets under custody, which is a meaningful but early-stage AUM. Voyager had grown much larger before collapse, but the deposit-custody model and early scaling dynamics are structurally comparable. |

Why similar:

The new pitch is a near-structural replica of Voyager Digital's core business: retail-facing crypto yield accounts funded by institutional lending. Both custody customer stablecoins and BTC, offer tiered interest rates, target US consumers, and monetize the spread between retail deposit rates and institutional borrowing rates. The fatal risk surface — counterparty concentration in institutional borrowers, liquidity mismatch, and regulatory ambiguity — is essentially identical.

Where diverged:

1. Lock-up tiers: The new pitch explicitly offers tiered interest by lock-up duration, which partially mitigates the instant-redemption liquidity mismatch that devastated Voyager; Voyager's accounts were effectively demand deposits with no lock-up friction. 2. Scale: The new pitch is at ~$50M AUM vs. Voyager's multi-billion dollar book at failure, meaning the founder still has time to build safeguards before systemic exposure grows. 3. Brokerage layer: Voyager operated a full crypto brokerage with trading, adding execution-risk and regulatory surface area the new pitch does not mention. Whether these differences are sufficient to avoid Voyager's fate depends entirely on how counterparty risk is managed.

Failure causes:

- single large-borrower concentration risk (Three Arrows Capital $666M default)
- liquidity mismatch between demand-deposit customer accounts and illiquid institutional loans
- no overcollateralization or borrower default protections disclosed
- regulatory non-compliance (false FDIC insurance claims)
- contagion from interconnected crypto counterparty failures (FTX collapse disrupted acquisition)
- inadequate capital buffers to absorb institutional loan losses
- absence of enforceable borrower covenants or collateral requirements

Lessons:

- Enforce strict borrower concentration limits — no single institutional borrower should represent more than 5-10% of total loan book to prevent a Three Arrows-style single-default death blow.
- Require overcollateralization and mark-to-market margin calls on all institutional loans; unsecured lending to crypto hedge funds is catastrophically exposed to correlated market crashes.
- Lock-up tiers reduce but do not eliminate liquidity risk — maintain a liquid reserve buffer (e.g., 20%+ of deposits) that is never lent out, so you can honor redemptions during stress.
- Never imply or state FDIC or government insurance coverage for crypto deposits; the Federal Reserve and FDIC will issue cease-and-desist orders and it destroys customer trust at the worst possible moment.
- Proactively engage SEC and state regulators before scaling; Voyager's regulatory ambiguity became a weapon used against it during bankruptcy proceedings and blocked acquisition deals.

Sources:



## FTX

Cryptocurrency exchange and hedge fund that collapsed in November 2022 after its founder secretly lent $10B of customer deposits to affiliated trading firm Alameda Research, triggering a bank run and the third-largest crypto bankruptcy in history.

Failure date: 2022-11-11
Lifespan: 42 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 7.5 | Both businesses custody customer crypto assets and deploy them to generate yield via lending to institutional counterparties/market makers. FTX routed customer deposits through Alameda Research (its affiliated market maker) in an undisclosed arrangement — structurally identical to the pitch's stated model of lending to institutional borrowers and market makers. The core risk profile (customer deposits funding institutional credit) is nearly the same. |
| market | 7.0 | Both target the US consumer crypto market and compete for crypto-native retail depositors seeking yield or trading services. FTX.US was the US-facing entity and actively marketed itself as a 'safe, easy way to get into crypto,' analogous to the pitch's consumer savings angle. The institutional borrower/market-maker funding ecosystem is shared. |
| gtm | 4.0 | FTX relied heavily on celebrity endorsements, sports sponsorships (FTX Arena, MLB, F1), and brand recognition at massive scale. The pitch describes a web+mobile product with tiered interest rates — a fintech savings-account GTM with no mention of celebrity marketing or exchange-volume-driven acquisition. The GTM strategies differ substantially. |
| stage_scale | 2.0 | FTX was at peak a $32B-valued exchange with 1M+ users, $10B daily trading volume, and hundreds of global affiliates. The pitch is at ~$50M AUM, an early-stage product. Scale differs by 3+ orders of magnitude. |

Why similar:

The structural core of the pitch — custody customer crypto assets (stablecoins and BTC), lend them to institutional borrowers and market makers, return yield to depositors — is precisely what FTX did covertly between its customer deposit base and Alameda Research. FTX's undisclosed lending of $10B in customer funds to Alameda is the fraudulent version of what the pitch proposes to do transparently. Both businesses sit at the intersection of retail crypto savings and institutional crypto credit markets. Both custodied stablecoins as a significant asset class. Both promised consumer-facing safety while running institutional credit risk in the background.

Where diverged:

1. Transparency of lending: The pitch explicitly discloses its lending model (tiered lock-up rates, institutional borrowers), whereas FTX concealed the Alameda relationship entirely — a fundamental governance divergence. 2. Business scope: The pitch is a pure savings/yield product; FTX was a full exchange, derivatives platform, and hedge fund — far broader and more complex. 3. Scale: The pitch holds ~$50M AUM vs. FTX's multi-billion dollar liability structure, meaning a potential failure would be orders of magnitude smaller. 4. Jurisdiction/structure: The pitch is described as a US consumer platform, implying US regulatory compliance; FTX was incorporated in Antigua, headquartered in the Bahamas, and deliberately sought offshore regulatory arbitrage. 5. Affiliated counterparty risk: FTX's critical failure point was lending to its own affiliated firm (Alameda); the pitch lends to third-party institutional borrowers, removing the self-dealing vector — provided that independence is maintained in practice.

Failure causes:

- undisclosed customer fund misappropriation to affiliated trading firm
- self-dealing between FTX and Alameda Research
- no separation between exchange and proprietary trading operations
- circular collateral (FTT token used as Alameda balance sheet asset)
- complete absence of corporate controls and independent oversight
- regulatory arbitrage via offshore incorporation undermining accountability
- confidence collapse and bank-run-style withdrawal spike triggered by public disclosure

Lessons:

- Publish real-time proof-of-reserves and third-party audits of custodied assets so customers can verify funds are not being silently re-lent beyond disclosed terms.
- Never lend customer deposits to a counterparty in which founders hold equity, debt, or profit-sharing interests — document and disclose all institutional borrower relationships and enforce strict third-party independence.
- Obtain explicit, written, informed consent from customers for each lock-up tier and lending arrangement, and honor withdrawal queues strictly — any gate on redemptions will be treated as a solvency signal.
- Pursue US regulatory licensing (money transmitter, state lending licenses, potential SEC/CFTC registration) proactively; offshore or ambiguous jurisdiction exposes the business to sudden regulatory shutdown and destroys customer trust overnight.
- Model and stress-test liquidity for a simultaneous withdrawal scenario at 20%+ of AUM within 72 hours; maintain a liquid reserve buffer that does not depend on institutional borrowers being able to return funds on demand.

Sources:



## MF Global

Major global derivatives broker that went bankrupt in 2011 after improperly transferring over $891 million in segregated customer funds to cover proprietary losses on leveraged European sovereign debt bets.

Failure date: 2011-10-31
Lifespan: 53 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 7.0 | Both businesses custody customer assets (deposits/collateral) and deploy them to generate yield or trading profit, creating a structural principal-agent tension where customer funds can be co-mingled with or pledged against house positions. MF Global used client money to fund proprietary repo trades; the pitch explicitly lends customer deposits to institutional borrowers — the core intermediation dynamic is nearly identical, including tiered/lock-up mechanics that mirror margin/segregation rules. |
| market | 4.0 | MF Global operated in regulated futures/derivatives markets serving institutional and retail commodity traders globally. The pitch targets US retail consumers seeking crypto savings yield. The customer profiles and asset classes differ substantially (fiat derivatives vs. stablecoins/BTC), though both sit in the broader 'yield on deposited assets' financial services space. |
| gtm | 3.0 | MF Global grew through institutional channels, acquisitions, and an IPO; it was a legacy brokerage spinning out of Man Group. The pitch is a consumer-first digital product (web + mobile, no account fees) targeting retail crypto holders — a fundamentally different distribution model with no acquisition-led growth history. |
| stage_scale | 5.0 | MF Global at bankruptcy had $7.3B in customer assets and was a publicly listed primary dealer. The pitch is custodying ~$50M — roughly 150x smaller. Both, however, are at a stage where real customer money is at risk and operational/compliance infrastructure is being stress-tested, making the failure modes structurally analogous despite the scale gap. |

Why similar:

Both businesses are fundamentally custodians-that-lend: they take in customer assets, promise a return, and re-deploy those assets to counterparties (institutional borrowers / market makers for the pitch; repo counterparties and sovereign-debt positions for MF Global). The core failure risk is identical — a shortfall between the assets owed to customers and the assets actually recoverable from borrowers. MF Global's collapse was triggered precisely by this mismatch when proprietary positions went bad and customer funds were used to plug the gap. The pitch, holding ~$50M in stablecoins and BTC lent to institutional borrowers, faces the same latent risk: if borrowers default or a liquidity crunch hits, the gap between customer claims and recoverable assets can become a regulatory and legal catastrophe overnight.

Where diverged:

1. Asset class: MF Global held fiat-denominated futures/derivatives and sovereign bonds; the pitch custodies crypto-native assets (stablecoins, BTC) that have no central-bank backstop and experience extreme intraday volatility. 2. Regulatory regime: MF Global was a CFTC-regulated primary dealer with segregation rules and exchange clearing; the pitch operates in the largely unregulated US crypto yield space with no equivalent federal framework for stablecoin deposit protection. 3. Business model origin: MF Global was a century-old brokerage giant ($7.3B AUC) that took on proprietary risk; the pitch is a early-stage consumer savings app with ~$50M AUC and no proprietary trading mandate stated. 4. Distribution: MF Global was institutionally distributed; the pitch is direct-to-consumer digital. 5. Leverage: MF Global was leveraged ~80:1 on proprietary positions; the pitch does not describe proprietary leverage, only pass-through lending.

Failure causes:

- improper co-mingling of customer and house funds
- excessive leverage on proprietary sovereign debt positions (~80:1)
- liquidity crisis triggered by repo counterparty demands
- inadequate segregation controls and internal oversight
- concentrated directional bet on correlated assets (PIIGS sovereign debt)
- management override of compliance safeguards
- regulatory and credit-rating downgrade cascade

Lessons:

- Maintain strict, auditable segregation of customer crypto assets from any operational or lending pool at all times — never allow even temporary co-mingling to cover liquidity shortfalls.
- Publish real-time or daily proof-of-reserves so customers and regulators can verify that custodied BTC and stablecoins match stated balances; opacity is what turned MF Global's problem into a scandal.
- Stress-test borrower default scenarios: if your institutional borrowers or market makers simultaneously fail to return assets, model whether you can still make all customer deposits whole, especially during crypto market dislocations.
- Avoid maturity or liquidity mismatch — if customers can withdraw on short notice (even with lock-up tiers), ensure the lending book has matching or shorter redemption windows so you are never forced to dip into other customers' funds.
- Engage proactively with regulators (CFTC, SEC, state money transmitter authorities) before reaching material scale; MF Global's lack of adequate oversight infrastructure made a bad situation catastrophic and criminal.

Sources:



---

Pipeline meta:

- cost_usd_total: 0.3283
- latency_ms_total: 126676
- trace_id: 01d4bd69-52e7-de50-544c-7f21af0a10df
- budget_remaining_usd: 1.6717
- budget_exceeded: False
- K_retrieve: 30
- N_synthesize: 5

Models:

- facet: anthropic/claude-haiku-4.5
- rerank: anthropic/claude-sonnet-4.6
- synthesize: anthropic/claude-sonnet-4.6
