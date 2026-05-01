# Premortem report for (unnamed)

Pitch: We're building a US consumer crypto savings platform that pays high yield on customer deposits by lending them to institutional borrowers and market makers. Web + mobile, no account fees, tiered interest rates by lock-up duration. Already custodying M of customer assets in stablecoins and BTC.

Generated: 2026-05-01T14:06:11.978447+00:00

## Top risks across all comparables

1. [HIGH] Secure securities-law clearance from SEC and state regulators before scaling US retail yield accounts.
   Applies because: The pitch describes a US consumer platform paying yield on crypto deposits (stablecoins and BTC) via institutional lending — exactly the product structure that triggered BlockFi's $100M SEC settlement, Voyager's cease-and-desist, and Bitconnect's state enforcement actions.
   Raised by: Celsius Network, BlockFi, FTX, Bitconnect (4/5)

2. [HIGH] Diversify institutional borrower counterparties; no single borrower should dominate the lending book.
   Applies because: The pitch explicitly states assets are lent to 'institutional borrowers and market makers' — a concentrated counterparty pool. BlockFi's FTX exposure and Voyager's 3AC exposure show a single default can wipe depositor funds entirely.
   Raised by: BlockFi, Voyager Digital, FTX (3/5)

3. [HIGH] Maintain audited, publicly verifiable proof-of-reserves so depositors and regulators can confirm solvency in real time.
   Applies because: The pitch mentions 'already custodying [M] of customer assets in stablecoins and BTC' — as AUM grows, absence of third-party proof-of-reserves invites the same opacity that destroyed Celsius, FTX, and Bitconnect's credibility.
   Raised by: Celsius Network, FTX, Bitconnect (3/5)

4. [HIGH] Set yield rates only at levels the actual loan book can sustainably fund; never advertise rates above what borrowers pay.
   Applies because: The pitch advertises 'high yield' on deposits — without explicitly tying rates to the loan book's actual returns, this mirrors the structural deficit that caused Celsius to pay yield from new deposits rather than lending income.
   Raised by: Celsius Network, Bitconnect (2/5)

5. [MEDIUM] Match deposit liquidity terms to loan durations — never fund on-demand withdrawals with illiquid institutional loans.
   Applies because: The pitch offers consumer deposits (implying withdrawal flexibility) funded by loans to institutional borrowers and market makers, with tiered lock-up durations — the exact mismatch that collapsed Celsius and Voyager when withdrawal demands exceeded liquid assets.
   Raised by: Celsius Network, Voyager Digital (2/5)

6. [MEDIUM] Keep liquid reserves sufficient to honor withdrawals without relying on any single counterparty credit facility.
   Applies because: The pitch describes lending customer assets (stablecoins and BTC) to institutional borrowers — if those borrowers are slow to repay or default, the platform needs its own liquidity buffer to meet consumer redemptions, as BlockFi and Voyager did not.
   Raised by: BlockFi, Voyager Digital (2/5)

7. [MEDIUM] Segregate customer deposits in bankruptcy-remote custodial accounts that cannot be lent to affiliated entities.
   Applies because: The pitch states the platform is 'already custodying [M] of customer assets' and lending them — without explicit segregation and bankruptcy-remote structuring, commingling risk mirrors FTX and Bitconnect patterns that led to DOJ investigations.
   Raised by: FTX, Bitconnect (2/5)

8. [MEDIUM] Build circuit-breaker policies (withdrawal gates, collateral top-up triggers) before a counterparty failure becomes a solvency crisis.
   Applies because: The pitch lends to 'institutional borrowers and market makers' with no mentioned contingency controls — without pre-defined circuit breakers, a single borrower stress event cascades directly to depositor harm, as seen at BlockFi.
   Raised by: BlockFi, Voyager Digital (2/5)

9. [MEDIUM] Never imply FDIC or SIPC deposit insurance coverage that does not legally apply to crypto yield accounts.
   Applies because: The pitch targets US consumers with a savings/deposit framing — this exact framing triggered regulatory cease-and-desist orders against Voyager and FTX for implying government deposit protection that did not exist.
   Raised by: Voyager Digital, FTX (2/5)

10. [MEDIUM] Stress-test the entire loan book for correlated crypto market drawdowns where borrower defaults and collateral values collapse simultaneously.
   Applies because: The pitch holds BTC and stablecoins and lends to market makers — in a crypto bear market, borrower solvency and collateral value are highly correlated, exactly the scenario that made Voyager's loan book unrecoverable in 2022.
   Raised by: Voyager Digital, Celsius Network (2/5)

## Celsius Network

Crypto yield platform that paid depositors high interest by lending assets to institutional borrowers and market makers, collapsed in 2022 amid a liquidity crisis and bankruptcy.

Failure date: 2022-01-01
Lifespan: 60 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.0 | Near-identical model: both accept consumer crypto deposits (stablecoins and BTC), pay tiered yield funded by lending to institutional borrowers/market makers, charge no account fees, and operate via web and mobile apps. Celsius explicitly offered up to 6.2% on BTC deposits and 0–8.95% on loans — the same rate band the pitch describes. |
| market | 9.0 | Both target US consumers seeking high yield on crypto holdings. Celsius had 1.7 million retail customers at peak; the pitch targets the same retail saver segment. Both operate in the cryptocurrency lending sub-sector with stablecoins and BTC as primary assets. |
| gtm | 7.0 | Both rely on a digital-first (web + mobile) self-serve acquisition funnel and use yield rates as the primary marketing hook. Celsius also used a proprietary CEL token and community referrals, which the pitch does not mention, creating a modest divergence. |
| stage_scale | 6.0 | The pitch describes an early-stage platform already custodying assets ('M of customer assets'); Celsius at comparable early stage had $3.3B AUM by end of 2020 and $12B by May 2022. Both started small but the pitch is clearly pre-scale, whereas the candidate document covers Celsius across its full growth arc. |

Why similar:

The pitch is functionally a replication of the Celsius model: consumer deposits in crypto (BTC and stablecoins), tiered yield by lock-up duration, no account fees, institutional lending as the yield source, and a web+mobile delivery channel. Every structural element of the business model — deposit-taking, lending spread, no-fee consumer interface — mirrors what Celsius built and what ultimately destroyed it.

Where diverged:

1. Token/CEL mechanism: Celsius paid interest partly in its proprietary CEL token and actively bought back CEL on the open market, creating circular token-price risk; the pitch does not mention a native token. 2. Scale and geography: Celsius operated globally with $12B AUM and 1.7M customers; the pitch is US-only and early-stage ('M' of assets). 3. Rehypothecation depth: Celsius was documented to have 'endlessly re-hypothecated assets … lending the same assets over and over,' which the pitch does not describe. 4. Revenue diversification: Celsius also earned from bitcoin mining and discretionary proprietary trading; the pitch appears to rely solely on the lending spread.

Failure causes:

- excessive rehypothecation and overleveraged balance sheet
- illiquidity mismatch — demand deposits funding illiquid or long-duration loans
- Ponzi-like yield sustainability — yields promised exceeded returns actually generated
- regulatory rejection — cease-and-desist orders from multiple US states for unregistered securities offerings
- founder fraud and CEL token market manipulation
- no deposit insurance or consumer protection backstop
- contagion from broader crypto market collapse (Luna/Terra crash) triggering bank-run withdrawal demand

Lessons:

- Match liability duration to asset duration — never fund liquid on-demand deposits with illiquid or locked institutional loans.
- Secure regulatory clarity on whether yield-bearing crypto accounts constitute securities before onboarding US retail customers, not after cease-and-desist orders arrive.
- Maintain verifiable, audited proof-of-reserves and publish them publicly so depositors and regulators can confirm solvency in real time.
- Set yield rates only at levels the actual loan book can sustainably fund; advertising rates above what borrowers pay is a structural deficit, not a growth strategy.
- Avoid proprietary token creation or self-purchasing schemes that create circular dependencies between platform solvency and token price.

Sources:

https://en.wikipedia.org/wiki/Celsius_Network

## BlockFi

US consumer crypto lending platform offering high-yield savings accounts backed by loans to institutional borrowers, valued at $3B before collapsing in FTX contagion.

Failure date: 2022-01-01
Lifespan: 60 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.5 | The new pitch is nearly identical to BlockFi's model: accept consumer deposits in crypto assets, pay tiered yield, fund that yield by lending to institutional borrowers and market makers. Both are digital lending platforms with no account fees and interest-rate differentiation by lock-up duration. |
| market | 9.5 | Both target US consumers seeking yield on crypto holdings (stablecoins and BTC explicitly named in the pitch), the exact sub-sector BlockFi operated in. The customer type, geography, and asset classes are a near-perfect match. |
| gtm | 8.0 | Both are web + mobile direct-to-consumer platforms acquiring retail depositors organically and via partnerships. BlockFi also relied heavily on brand/marketing spend; the pitch implies a similar self-serve funnel. No major structural GTM divergence is evident from available data. |
| stage_scale | 6.0 | BlockFi reached $3B valuation and hundreds of thousands of creditors; the new pitch is early-stage ('already custodying M of customer assets'). Scale is meaningfully different, but the trajectory and stage arc are analogous — both started by accumulating retail deposits before institutional credit lines. |

Why similar:

The new pitch replicates BlockFi's core architecture almost exactly: take consumer crypto deposits, pay high yield funded by institutional lending, offer tiered rates by lock-up, operate as a US-domiciled web/mobile platform with no account fees. Even the specific asset classes (stablecoins, BTC) and borrower profile (institutional borrowers, market makers) match BlockFi's disclosed business model.

Where diverged:

The pitch does not describe the same degree of concentrated counterparty exposure that destroyed BlockFi (a $400M credit facility from a single exchange, FTX). Whether the new pitch has diversified counterparty risk is unstated. Additionally, the new pitch is at an early stage ('M of customer assets') vs. BlockFi's $3B-valuation scale, which may mean regulatory obligations are not yet fully triggered — but also means the runway to absorb a credit event is far thinner. No divergence in product design or market segment is apparent from the pitch text.

Failure causes:

- Concentrated counterparty exposure to FTX (single point of failure in lending book)
- Contagion from FTX bankruptcy causing withdrawal halt and loss of customer funds access
- Regulatory non-compliance fines ($100M SEC/state settlement for unregistered securities)
- Uninsured cash deposits at Silicon Valley Bank ($227M exposed)
- Inability to independently survive a simultaneous credit-market and exchange collapse
- Over-reliance on a single rescue deal (FTX buyout option) that evaporated

Lessons:

- Diversify institutional borrower counterparties aggressively — no single borrower or exchange should represent more than a small fraction of the lending book.
- Engage securities regulators proactively before scaling; BlockFi's $100M settlement for unregistered loan products is a precedent that will be applied to identical products.
- Maintain liquid reserves sufficient to honor withdrawal demands without relying on a credit facility from any single counterparty.
- Keep custodied cash in FDIC-insured accounts or equivalents; BlockFi's $227M exposure to SVB shows operational cash is a secondary risk vector.
- Build explicit circuit-breaker policies (withdrawal gates, collateral top-up triggers) that activate before a counterparty failure becomes a solvency crisis.

Sources:

https://en.wikipedia.org/wiki/BlockFi

## Voyager Digital

US consumer cryptocurrency brokerage and lending platform that paid yield on deposits by extending loans to institutional borrowers, collapsing in July 2022 after Three Arrows Capital defaulted on $666 million in loans.

Failure date: 2022-01-01
Lifespan: 48 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.0 | Both businesses take consumer crypto deposits and on-lend them to institutional borrowers/market makers to generate yield, offering tiered or reward-based interest rates. The core deposit-and-lend flywheel is nearly identical, even if the new pitch foregrounds stablecoins and BTC savings explicitly while Voyager was branded as a brokerage. |
| market | 9.0 | Both target US retail consumers seeking crypto yield, operating in the same regulatory environment (SEC, Fed, FDIC scrutiny) with the same pool of institutional crypto borrowers and market makers as counterparties. |
| gtm | 7.0 | Both use web and mobile with no account fees as the acquisition hook and rely on high advertised yield to grow deposits organically. Voyager also had a loyalty-rewards layer. The pitch is slightly earlier-stage but the go-to-market motion is materially the same. |
| stage_scale | 6.0 | The new pitch already custodies assets ('M of customer assets') suggesting it is post-launch but pre-scale; Voyager had grown to $1.3B+ of customer assets before failure. Both are past zero-revenue stage but the new pitch is considerably smaller in AUM. |

Why similar:

The new pitch is structurally the same business as Voyager Digital: accept consumer crypto deposits, lend them to institutional counterparties at higher rates, and pass a portion of that spread back to depositors as advertised yield. Both operate in the US consumer market, use a no-fee web/mobile product, and depend on a small number of large wholesale borrowers to generate returns that fund retail payouts.

Where diverged:

1. Lock-up tiers: the new pitch explicitly structures deposits by lock-up duration, creating term-matched risk; Voyager did not publicly emphasize lock-up tranches. 2. Asset focus: the new pitch foregrounds stablecoins and BTC, whereas Voyager was a broader brokerage supporting 100+ tokens, meaning the new pitch carries less token-diversity risk but higher stablecoin-specific regulatory risk. 3. Scale: the new pitch is at an early 'M of assets' stage versus Voyager's $1B+ AUM at failure, so concentration risk in a single counterparty default would be proportionally more acute sooner. 4. Monetization: Trusted facts classify Voyager's monetization as transaction_fee; the new pitch describes a spread/yield model with no explicit trading fees, which is a different primary revenue line.

Failure causes:

- Concentrated counterparty default (Three Arrows Capital $666M unpaid loan)
- Liquidity mismatch between demand deposits and locked institutional loans
- False and misleading FDIC insurance claims drawing federal cease-and-desist
- Contagion from correlated crypto market crash (2022 bear market)
- Regulatory non-compliance with banking and securities disclosure rules
- Acquisition rescue collapsed due to FTX bankruptcy
- Insufficient loss-absorption reserves against wholesale credit risk

Lessons:

- Diversify institutional borrower exposure — never allow a single counterparty to represent more than a small fraction of total loan book, or a single default can wipe out all depositor funds.
- Match deposit liquidity terms to loan durations — if consumers can withdraw on demand, do not lend all assets in uncollateralized long-term loans to institutional counterparties.
- Never imply or state government deposit insurance (FDIC/SIPC) coverage that does not legally apply — regulators will issue cease-and-desist orders and it accelerates loss of consumer trust at exactly the wrong moment.
- Stress-test the entire loan book for correlated crypto market drawdowns — institutional borrowers and the collateral backing their loans can both collapse simultaneously in a bear market.
- Maintain a visible, segregated liquidity reserve so that deposit redemptions can be honored even during a partial counterparty default, and disclose reserve ratios to customers proactively.

Sources:

https://en.wikipedia.org/wiki/Voyager_Digital

## FTX

Cryptocurrency exchange and hedge fund that collapsed in 2022 after fraudulent misappropriation of customer funds by its founder Sam Bankman-Fried.

Failure date: 2022-01-01
Lifespan: 36 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 6.0 | Both platforms take custody of customer crypto assets (stablecoins and BTC appear in both) and deploy them to generate yield/returns — FTX via Alameda Research as a market-maker/trading counterparty, the new pitch via lending to institutional borrowers and market makers. The new pitch is explicitly a savings/yield product rather than a trading exchange, but the core mechanic of custodying customer deposits and re-deploying them to a related or third-party borrower is structurally very similar and is exactly the risk vector that destroyed FTX. |
| market | 6.0 | Both operate in the US consumer crypto market (FTX via FTX.US, new pitch directly). Both custody stablecoins and BTC for retail customers. FTX was global with a US-specific subsidiary; the new pitch is US-only per trusted facts (geography: us implied by pitch prose and customer_type: consumer). The high-yield savings angle targets a somewhat narrower segment than FTX's broad trading audience, but the underlying market — retail crypto holders seeking returns — overlaps substantially. |
| gtm | 4.0 | FTX pursued aggressive mass-market GTM: stadium naming rights, celebrity endorsements, sports sponsorships, and a broad trading app. The new pitch describes a web+mobile savings product with tiered interest rates and lock-up durations, suggesting a more fintech/savings-app GTM rather than brand-splash sports marketing. Overlap exists in targeting consumer crypto holders online, but the channel and positioning strategies appear materially different. |
| stage_scale | 3.0 | FTX reached a $32B valuation with $10B+ daily trading volume and 1M+ users before collapse. The new pitch is early-stage, already custodying an unspecified 'M of customer assets' — almost certainly millions of dollars, orders of magnitude smaller. Stage and scale diverge significantly. |

Why similar:

Both FTX and the new pitch share the same structural risk: they take custody of customer crypto deposits (stablecoins and BTC) and re-deploy those assets to counterparties (Alameda Research for FTX; institutional borrowers and market makers for the new pitch) in order to generate returns that are passed back to customers. This rehypothecation model — where customer funds are lent out and the platform's solvency depends on counterparty repayment — is the precise mechanism that caused FTX's collapse. Both also target consumer-facing crypto audiences in the US and custody stablecoins.

Where diverged:

1. Product type: FTX was a full trading exchange with derivatives, spot markets, and an exchange token (FTT); the new pitch is a pure savings/yield product with no trading or native token. 2. Monetization: FTX monetized via transaction fees; the new pitch uses tiered interest rate spread (the difference between borrowing cost to the platform and yield paid to customers). 3. Geography: FTX was global at scale; the new pitch is US-only. 4. Scale: FTX was a $32B valuation platform; the new pitch is early-stage with modest AUM. 5. Fraud: FTX's collapse was driven by deliberate, criminal misappropriation of funds by insiders; the new pitch does not exhibit that intent — but the structural custody-and-redeploy model creates the same counterparty and transparency risks even without fraud.

Failure causes:

- fraudulent misappropriation of customer funds to affiliated trading firm (Alameda Research)
- circular balance sheet: exchange token (FTT) used as collateral by affiliated entity
- complete absence of corporate controls and independent financial oversight
- bank-run liquidity crisis when counterparty insolvency became public
- regulatory investigation and FDIC misrepresentation cease-and-desist
- contagion to affiliated lenders (BlockFi, Genesis) amplifying collapse
- concentration of control in inexperienced, unsophisticated insiders

Lessons:

- Segregate customer deposits in bankruptcy-remote custodial accounts that cannot be lent to any affiliated entity without explicit, audited customer consent.
- Obtain and publish regular third-party proof-of-reserves and independent audits of all counterparty loan books before scaling AUM.
- Never use any platform-native asset or token as collateral for customer-deposit-backed loans — circular collateral structures are catastrophic in a liquidity crisis.
- Ensure your institutional borrower and market-maker counterparties have transparent, disclosed balance sheets and that loan concentration limits are contractually enforced.
- Proactively engage US regulators (CFTC, SEC, state banking regulators) on whether your yield product constitutes a securities offering or uninsured banking product, and do not imply FDIC coverage.

Sources:

https://en.wikipedia.org/wiki/FTX

## Bitconnect

A cryptocurrency lending/staking platform that promised high daily interest yields via an opaque 'trading bot', later proven to be a Ponzi scheme that defrauded investors of $2.4 billion before collapsing in January 2018.

Failure date: 2018-01-01
Lifespan: 24 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 7.0 | Both platforms accept consumer crypto deposits and promise yield via lending; both use lock-up durations and tiered returns. The surface mechanism is nearly identical — customers hand over crypto assets in exchange for interest. The key difference is that Bitconnect's yield was fabricated (Ponzi), whereas the new pitch claims legitimate institutional lending. The structural wrapper is the same. |
| market | 8.0 | Both target US consumers (Bitconnect was globally distributed but heavily US-facing, per the SEC/DOJ charges and the US cease-and-desist actions). Both operate in the crypto savings/yield niche. Customer type (consumer) and product type (crypto lending platform) match directly per Trusted facts. |
| gtm | 4.0 | Bitconnect relied heavily on MLM-style referral promoters and influencer testimonials to drive growth. The new pitch does not describe MLM or promoter networks — it describes a direct web/mobile product. GTM approach appears structurally different, though both target retail crypto holders. |
| stage_scale | 5.0 | Bitconnect grew to a top-20 cryptocurrency by market cap. The new pitch is early-stage, custodying an unspecified amount of customer assets ('M of customer assets'). Scale is substantially smaller. Both were/are pre-institutional-regulation compliance stage at the point of comparison. |

Why similar:

Both are US-facing consumer crypto yield platforms built on the same core value proposition: deposit crypto, earn interest through a lending mechanism, with tiered lock-up durations. The customer type (consumer), product type (crypto lending), and geography (US / global with US regulatory exposure) match. The pitch's model — accepting stablecoin and BTC deposits and paying yield to consumers — is the exact surface pattern regulators associated with Bitconnect and later with Celsius, BlockFi, and Voyager. The SEC's case against Bitconnect specifically targeted the 'lending program' structure as an unregistered securities offering, which is the same regulatory surface the new pitch inhabits.

Where diverged:

1. Legitimacy of yield source: The new pitch explicitly claims yield is generated by lending to institutional borrowers and market makers — a real, auditable credit mechanism — whereas Bitconnect's yield came from an opaque 'trading bot' that was later proven fictitious. 2. Asset composition: The new pitch custodies stablecoins and BTC; Bitconnect required users to convert BTC into its proprietary BCC token, creating a captive, illiquid asset trap. 3. MLM structure: Bitconnect used a multi-level referral/promoter network; the new pitch describes no such structure. 4. Regulatory posture: The new pitch does not mention proactive securities registration or exemption strategy, but it does not appear to use promoters or fabricate returns, which were the specific triggers for Bitconnect's cease-and-desist orders.

Failure causes:

- fraudulent yield mechanism (fabricated 'trading bot' returns)
- unregistered securities offering
- multi-level marketing / promoter network amplified fraud
- regulatory cease-and-desist from Texas and North Carolina securities regulators
- proprietary BCC token created illiquid captive asset
- complete lack of operational transparency
- founder and key promoters indicted for wire fraud and money laundering

Lessons:

- Register or obtain a legal opinion on whether your lending product constitutes a securities offering before onboarding the first dollar — Bitconnect was killed by state cease-and-desist orders, not market forces.
- Make yield sources fully auditable and disclosed to customers; any opacity around how interest is generated will trigger the same 'Ponzi' label that destroyed Bitconnect and later Celsius.
- Do not use tiered referral or promoter incentive structures to acquire customers — that pattern is a regulatory red flag that co-mingled with Bitconnect's fraud charges.
- Custody assets in segregated, third-party-custodied accounts and publish proof-of-reserves; co-mingling or opaque custody is the fastest path to a DOJ investigation in this product category.
- Proactively engage the SEC and state securities regulators with a no-action letter request or Reg D/Reg A filing before scaling — the institutional lending wrapper does not automatically exempt you from securities law.

Sources:

https://en.wikipedia.org/wiki/Bitconnect

---

Pipeline meta:

- cost_usd_total: 0.3065
- latency_ms_total: 121345
- trace_id: c4b82526-3c48-a20b-0bd9-c51350584f79
- budget_remaining_usd: 1.6935
- budget_exceeded: False
- K_retrieve: 30
- N_synthesize: 5
- min_similarity_score: 4.0

Models:

- facet: anthropic/claude-haiku-4.5
- rerank: anthropic/claude-sonnet-4.6
- synthesize: anthropic/claude-sonnet-4.6