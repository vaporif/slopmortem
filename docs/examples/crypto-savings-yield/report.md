# Slopmortem report for (unnamed)

Pitch: We're building a US consumer crypto savings platform that pays high yield on customer deposits by lending them to institutional borrowers and market makers. Web + mobile, no account fees, tiered interest rates by lock-up duration. Already custodying M of customer assets in stablecoins and BTC.

Generated: 2026-05-01T20:24:36.332064+00:00

## Top risks across all comparables

1. [HIGH] Treat yield-bearing crypto accounts as securities from day one; get registration or legal clarity before scaling.
   Applies because: The pitch explicitly describes a US consumer platform paying yield on crypto deposits (stablecoins and BTC) via lending — the exact product structure the SEC and state regulators have targeted as unregistered securities in Celsius, BlockFi, and Bitconnect enforcement actions.
   Raised by: Celsius Network, BlockFi, Bitconnect, FTX (4/5)

2. [HIGH] Match lock-up tier durations to loan book liquidity; never promise withdrawals you cannot fund under stress.
   Applies because: The pitch features 'tiered interest rates by lock-up duration' and lends to institutional borrowers — the exact asset-liability mismatch that triggered bank-run collapses at Celsius, BlockFi, Voyager, and FTX.
   Raised by: Celsius Network, BlockFi, Voyager Digital, FTX (4/5)

3. [HIGH] Maintain an audited liquid reserve sufficient to cover a simultaneous run; never fully deploy all customer assets.
   Applies because: The pitch already custodies 'M of customer assets' in stablecoins and BTC deployed via lending — inability to meet withdrawals under stress was the proximate trigger for Celsius, BlockFi, and Voyager bankruptcies.
   Raised by: Celsius Network, BlockFi, Voyager Digital (3/5)

4. [HIGH] Diversify institutional borrowers with hard concentration limits; a single counterparty default must not be existential.
   Applies because: The pitch states it lends customer assets to 'institutional borrowers and market makers' — single-counterparty concentration was the proximate cause of collapse at BlockFi (Alameda/FTX) and Voyager (Three Arrows).
   Raised by: BlockFi, Voyager Digital (2/5)

5. [MEDIUM] Publish regular third-party proof-of-reserves and loan book attestations; opacity destroys trust and invites regulatory action.
   Applies because: The pitch custodies real customer assets in stablecoins and BTC and deploys them to institutional borrowers — lack of independent verification of custody and loan quality was a first-order failure at Celsius, FTX, and Bitconnect.
   Raised by: Celsius Network, FTX, Bitconnect (3/5)

6. [MEDIUM] Never imply FDIC or government deposit insurance coverage; ensure all marketing and disclosures are explicit about this.
   Applies because: The pitch targets US consumers with a 'savings platform' — the savings framing is precisely what triggered Voyager's and FTX's FDIC cease-and-desist actions.
   Raised by: Voyager Digital, FTX (2/5)

7. [MEDIUM] Segregate customer deposits from any affiliated trading entity and prove segregation via third-party audit.
   Applies because: The pitch lends customer assets to 'market makers,' creating a structural risk that customer funds could commingle with trading operations — the exact pattern that destroyed FTX.
   Raised by: FTX (1/5)

8. [MEDIUM] Prove yield generation with audited documentation of borrower relationships before scaling; preempt Ponzi allegations.
   Applies because: The pitch offers 'high yield' on deposits funded by lending to institutional borrowers — without transparent, audited proof of yield source, this structurally resembles Bitconnect and invites regulatory and reputational attack.
   Raised by: Bitconnect (1/5)

9. [MEDIUM] Publish a detailed public risk-disclosure document covering counterparty default and liquidation scenarios before launch.
   Applies because: The pitch describes lending to institutional borrowers with tiered lock-ups — regulators and customers will demand clear disclosure of what happens to user funds if a borrower defaults, and proactive disclosure differentiates the platform from Bitconnect-style bad actors.
   Raised by: Bitconnect (1/5)

10. [LOW] Avoid existential dependency on a single strategic investor or credit line; diversify funding sources.
   Applies because: The pitch is an early-stage platform already custodying customer assets — reliance on a single backer's credit line was how BlockFi's FTX relationship became fatal.
   Raised by: BlockFi (1/5)

## Celsius Network

Crypto lending and deposit platform paying high yield on customer crypto by relending to institutional borrowers; collapsed in 2022 after a bank run exposed a $1.2B balance-sheet deficit.

Failure date: 2022-01-01
Lifespan: 60 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.5 | Near-identical model: accept consumer crypto deposits (BTC, stablecoins), pay tiered yield by relending to institutional borrowers and market makers, no account fees, mobile + web delivery. The pitch mirrors Celsius almost exactly, including the institutional lending wedge and tiered rates. |
| market | 9.0 | Same sub-sector (crypto lending/deposits), same customer type (consumer), same product category (yield-bearing crypto savings), same asset classes (BTC and stablecoins). Celsius was global; the pitch is US-focused, a minor geographic narrowing. |
| gtm | 8.0 | Both target retail crypto holders seeking higher yield than banks, using a digital-first (mobile + web) self-serve funnel with no fees as the primary acquisition hook. Celsius also used a native CEL token for boosted yields, which the pitch does not mention. |
| stage_scale | 6.0 | Celsius had ~$12B AUM and 1.7M customers at peak before failure; the pitch is early-stage with 'M of customer assets' (presumably millions, not billions). Similar model at a much earlier scale, which creates both a liability (unproven unit economics) and an opportunity (time to course-correct). |

Why similar:

The new pitch is structurally a rebuild of Celsius: consumer-facing crypto savings product, yield funded by lending deposited assets to institutional borrowers, tiered rates by lock-up, no fees, BTC and stablecoin custody, web + mobile. Every core architectural element of the Celsius business model is present in the pitch.

Where diverged:

1. Geography: the pitch explicitly targets US consumers only, whereas Celsius operated globally across multiple countries. 2. Scale: Celsius had $8–12B AUM at collapse; the pitch is at an early 'M' of assets, meaning the leverage and systemic risk are far lower today. 3. No native token: the pitch does not mention a proprietary token (Celsius's CEL token was central to its yield-boosting mechanics and became a vehicle for alleged market manipulation and insider selling). 4. Regulatory posture: the pitch does not describe any specific regulatory compliance framework, but post-2022 the US regulatory environment for crypto lending is dramatically more defined, meaning the new founder must proactively address it rather than react. 5. No stated re-hypothecation or discretionary trading: the pitch implies straightforward institutional lending, whereas Celsius also used discretionary crypto trading and aggressive re-hypothecation to juice yields.

Failure causes:

- excessive leverage and re-hypothecation of customer assets
- liquidity mismatch between illiquid deployed assets and on-demand withdrawal obligations
- no deposit insurance or regulatory backstop
- unregistered securities offering in multiple US states triggering cease-and-desist orders
- Ponzi-like yield structure dependent on continuous new deposits
- insider fraud and token market manipulation by CEO
- contagion from broader crypto market collapse (Luna/UST implosion) wiping out deployed assets

Lessons:

- Maintain a credible, audited liquidity reserve sufficient to cover a simultaneous withdrawal of your largest depositor cohort — do not fully deploy all customer assets.
- Register or obtain legal clarity on whether your yield-bearing accounts constitute securities before launching in any US state; do not wait for cease-and-desist orders.
- Publish regular third-party attestations of asset custody, deployment strategy, and loan book quality so customers can verify solvency claims independently.
- Never tie founder or employee compensation to a proprietary token whose price depends on the platform's continued growth — it creates catastrophic conflict-of-interest and fraud risk.
- Design your lock-up tiers and withdrawal terms so they match the actual liquidity profile of your loan book; do not promise liquidity you cannot deliver under stress conditions.

Sources:

https://en.wikipedia.org/wiki/Celsius_Network

## BlockFi

Crypto savings and lending platform offering high-yield accounts on digital assets by on-lending deposits to institutional borrowers, collapsed in 2022 after contagion from FTX's bankruptcy wiped out access to funds.

Failure date: 2022-01-01
Lifespan: 60 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.5 | The new pitch is a near-identical model: consumer deposits in crypto/stablecoins earn tiered yield, funded by lending to institutional borrowers and market makers — exactly BlockFi's core mechanism. Web + mobile delivery, no account fees, and tiered rates by lock-up duration all mirror BlockFi's product surface. |
| market | 9.5 | Both target US consumers seeking yield on crypto holdings (stablecoins and BTC specifically named in both cases). The customer type (consumer), geography (US), and sub-sector (cryptocurrency lending) are identical per Trusted facts. |
| gtm | 8.0 | Both rely on direct consumer acquisition for a savings/yield product in the US crypto market. BlockFi used influencer marketing, referral programs, and brand advertising — the new pitch does not specify GTM but the product form factor (web + mobile, no fees) is consistent with the same self-serve acquisition playbook. |
| stage_scale | 6.0 | BlockFi reached a $3 billion valuation with hundreds of thousands of creditors before failure, indicating significant scale. The new pitch is custodying an unspecified 'M of customer assets' suggesting early traction but much earlier stage — similar model, meaningfully different scale. |

Why similar:

Both are US consumer crypto savings platforms built on the same fundamental business model: aggregate retail deposits in digital assets (stablecoins and BTC), pay high yield funded by lending those assets to institutional counterparties. Both offer tiered interest rates, no account fees, and web+mobile access. BlockFi is the canonical archetype of this exact model, making it the most directly comparable failed precedent.

Where diverged:

Stage and scale: BlockFi was a $3B-valued company with 100,000+ creditors at failure; the new pitch is at early custodied-assets stage. The new pitch also does not appear to have yet entered the regulatory settlement that BlockFi faced ($100M SEC/state settlement in Feb 2022 for unregistered securities), though this gap may close quickly given the identical product structure.

Failure causes:

- concentrated counterparty exposure to FTX
- cascading contagion from FTX bankruptcy November 2022
- uninsured cash deposits at Silicon Valley Bank ($227M)
- prior $100M regulatory settlement for unregistered securities offering
- over-reliance on single credit facility (FTX $400M rescue package)
- inability to withstand simultaneous crypto market and counterparty shocks

Lessons:

- Diversify institutional borrower counterparties aggressively — single-counterparty concentration (as with Alameda/FTX) can be fatal even if your own books are clean.
- Treat regulatory compliance for yield-bearing crypto accounts as day-one existential risk: the SEC and state regulators view these products as unregistered securities; get ahead of registration rather than settle for $100M later.
- Maintain liquid reserves and stress-test for simultaneous platform-run and counterparty default scenarios — BlockFi's inability to return withdrawals was the proximate trigger for bankruptcy.
- Do not place operating cash in uninsured bank deposits at a single institution; diversify and stay under FDIC limits or use treasuries.
- Avoid existential dependency on a single strategic investor or acquirer's credit line — BlockFi's $400M FTX lifeline became its noose when FTX collapsed.

Sources:

https://en.wikipedia.org/wiki/BlockFi

## Voyager Digital

US cryptocurrency brokerage that offered high-yield lending on customer deposits, collapsed in July 2022 after counterparty Three Arrows Capital defaulted on $666M in loans.

Failure date: 2022-01-01
Lifespan: 48 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.0 | Both are US consumer-facing crypto platforms that take custody of customer assets (stablecoins and BTC) and deploy them to institutional borrowers/market makers to generate yield. The core value proposition — earn interest on crypto deposits via institutional lending — is essentially identical. |
| market | 9.0 | Same geography (US), same customer type (consumer retail), same asset classes (crypto including BTC and stablecoins), and the same macro moment of crypto yield demand. The addressable market and competitive set are virtually indistinguishable. |
| gtm | 7.0 | Both target retail consumers via web and mobile, emphasizing yield/interest rates as the primary hook. Voyager also used tiered rewards. The new pitch's tiered interest by lock-up duration mirrors Voyager's loyalty/yield structure, though exact acquisition channels are not detailed in the candidate document. |
| stage_scale | 6.0 | The new pitch cites custodying 'M of customer assets' (presumably millions) in an early stage; Voyager had reached $1.3B+ in customer assets before collapse. Both had real AUM but Voyager was further along. Stage is broadly similar (post-launch, growing AUM) but Voyager was at much larger scale at failure. |

Why similar:

The new pitch is structurally near-identical to Voyager Digital: a US consumer crypto savings/yield product that custody customer assets (stablecoins and BTC) and lends them to institutional borrowers and market makers to fund high yields, offered via web and mobile with tiered rate structures. The failure mode Voyager encountered — institutional counterparty default wiping out depositor funds — is directly relevant to the new pitch's risk profile.

Where diverged:

1. Lock-up tiers: The new pitch explicitly uses duration-based lock-up tiers to manage liquidity, whereas Voyager offered more liquid reward structures; this may reduce liquidity mismatch risk. 2. Scale: The new pitch is at a much earlier AUM stage ('M' vs. Voyager's $1.3B+), meaning systemic contagion risk is lower today but also that the business has not yet proven it can survive a credit cycle. 3. Monetization label: Trusted facts classify Voyager's monetization as transaction_fee (brokerage), while the new pitch is purely a yield/savings product with no explicit trading fees mentioned — a narrower, more deposit-focused model. 4. Regulatory posture: The new pitch does not describe any specific regulatory framework or custodial structure, whereas Voyager's post-mortem highlighted FDIC misrepresentation as a distinct regulatory failure.

Failure causes:

- Counterparty concentration risk (single borrower Three Arrows Capital held $666M)
- Institutional borrower default triggering insolvency
- Liquidity mismatch between demand deposits and illiquid loans
- Misleading FDIC insurance claims drawing regulatory cease-and-desist
- Crypto market contagion (FTX collapse derailed bankruptcy rescue)
- Insufficient credit underwriting of institutional borrowers
- Customer asset co-mingling making recovery partial and delayed

Lessons:

- Diversify institutional borrower exposure with hard per-counterparty concentration limits — a single default should not be existential.
- Never imply or state government deposit insurance (FDIC/SIPC) coverage unless you are a chartered bank; regulatory action will compound a liquidity crisis.
- Structure lock-up tiers so that asset duration matches liability duration — ensure you can never be forced to sell illiquid positions to meet withdrawals.
- Perform rigorous, ongoing credit due diligence on all institutional borrowers; require over-collateralization and real-time margin calls.
- Maintain a liquid reserve buffer sufficient to survive a correlated crypto market downturn without triggering a withdrawal freeze.

Sources:

https://en.wikipedia.org/wiki/Voyager_Digital

## FTX

Crypto exchange and hedge fund that collapsed in 2022 after founder Sam Bankman-Fried secretly loaned $10B of customer deposits to affiliated trading firm Alameda Research, triggering a bank run and Chapter 11 bankruptcy.

Failure date: 2022-01-01
Lifespan: 36 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 6.0 | Both businesses custody customer crypto assets and generate yield/revenue by deploying those assets to institutional counterparties (market makers, trading firms). However, FTX's primary monetization was transaction fees on a trading exchange, while the new pitch is explicitly a savings/yield product with tiered lock-up rates — closer to BlockFi or Celsius than to FTX proper. The structural risk (customer funds lent to institutional borrowers) is nearly identical. |
| market | 5.0 | Both operate in the US consumer crypto market and hold stablecoins and BTC. FTX was global with a US subsidiary; the new pitch is US-only. Both target retail users seeking yield/returns on crypto holdings. The institutional-borrower/market-maker lending model is the same underlying market structure. |
| gtm | 3.0 | FTX used massive celebrity sponsorships, sports naming rights, and a 'safe, easy way to get into crypto' branding campaign. The new pitch describes a web+mobile product with no account fees and tiered rates — a more organic, product-led GTM with no mention of celebrity endorsement or aggressive brand spend. |
| stage_scale | 4.0 | FTX peaked at $32B valuation, 1M+ users, and $10B daily trading volume — far beyond an early-stage savings platform. The new pitch is at an early traction stage ('already custodying M of customer assets'). Stage and scale are materially different, though both were/are handling live customer funds in crypto. |

Why similar:

The structural core is nearly identical: customer crypto deposits (including stablecoins and BTC) are custodied by the platform and lent to institutional borrowers/market makers to generate yield returned to retail users. FTX's Alameda Research functioned exactly as this model — as the institutional borrower of customer funds. Both face the same fundamental trust and liquidity risk: if customers demand withdrawals faster than the institutional lending book can be unwound, a bank run occurs. Both serve retail consumers in the crypto space and hold stablecoins as a key asset class.

Where diverged:

1. Product type: FTX was a trading exchange (monetized via transaction fees); the new pitch is a dedicated savings/yield product (monetized via interest spread), making it structurally closer to BlockFi or Celsius. 2. Geography: FTX was global; the new pitch is US-only, implying direct exposure to US consumer financial regulations from day one. 3. Scale: FTX was at $32B valuation with 1M+ users at its peak; the new pitch is early-stage with an undisclosed but small asset base. 4. Fraud: FTX's collapse was driven by deliberate, undisclosed fraud (secret transfer of customer funds); the new pitch appears to describe a disclosed lending model with tiered lock-ups, which is a different governance posture — though disclosed lending does not eliminate run risk.

Failure causes:

- Fraudulent misappropriation of customer funds to affiliated trading firm (Alameda Research)
- Circular balance sheet — native FTT token used as collateral, creating reflexive insolvency risk
- Complete absence of corporate controls and independent oversight
- Liquidity mismatch — illiquid investments funded by liquid customer deposits
- Concentrated control in a small group of inexperienced insiders
- Bank run triggered by public disclosure of balance sheet weakness
- Regulatory arbitrage through offshore domicile that ultimately failed to shield operations

Lessons:

- Segregate customer deposits from any affiliated trading or investment entity and prove segregation via third-party audit, or regulators and customers will assume the worst.
- Never use a proprietary token or illiquid asset as collateral backing customer liabilities — a stablecoin and BTC-backed book must be stress-tested for simultaneous withdrawal scenarios.
- Obtain explicit legal clarity on whether your yield product constitutes a security or unregistered deposit-taking in every US state before launching, since FTX received an FDIC cease-and-desist for implying deposit insurance it did not have.
- Publish real-time proof-of-reserves for custodied assets; opacity in crypto custody is now a first-order customer trust and regulatory risk after FTX.
- Structure lock-up durations so the liability maturity profile matches or is shorter than the loan book maturity — mismatches between liquid liabilities and illiquid lending was the proximate cause of FTX's bank-run collapse.

Sources:

https://en.wikipedia.org/wiki/FTX

## Bitconnect

A cryptocurrency high-yield lending platform that promised daily compounded interest returns funded by a proprietary trading bot — later confirmed as a $2.4B Ponzi scheme.

Failure date: 2018-01-01
Lifespan: 24 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 7.0 | Both platforms custody consumer crypto assets (BTC and stablecoins in the pitch; BCC and Bitcoin in Bitconnect) and promise yield via lending/lock-up mechanisms. The surface structure — deposit crypto, earn tiered interest by lock-up duration — is nearly identical to Bitconnect's lending platform structure. The key stated difference is that the new pitch claims institutional borrowers and market makers as the counterparty rather than an opaque trading bot, but the business model shape is highly similar. |
| market | 8.0 | Both target consumer crypto holders seeking yield in the US (and globally for Bitconnect). The macro market context — retail investors attracted to high crypto yields — is the same. Bitconnect was global but had heavy US retail presence and faced US regulatory action; the new pitch is explicitly US consumer. |
| gtm | 5.0 | Bitconnect relied heavily on multi-level marketing and affiliate promoters to acquire users. The new pitch describes a web+mobile product with no explicit MLM or referral structure, suggesting a more conventional fintech GTM. However, both rely on yield as the primary user acquisition hook, which is a meaningful overlap. |
| stage_scale | 5.0 | Bitconnect grew to billions of dollars in investor funds before collapsing. The new pitch is at an earlier stage, already custodying an unspecified amount ('M of customer assets') — likely millions, not billions. Stage is meaningfully different, though both are post-launch with real customer assets under management. |

Why similar:

The new pitch mirrors Bitconnect's core mechanics almost exactly on the surface: consumers deposit cryptocurrency (BTC and stablecoins), assets are locked up for a set duration, and tiered interest is paid. Both operate in the US consumer crypto market and use yield as the primary value proposition. Regulators and investors scrutinizing the new platform will inevitably draw the Bitconnect comparison given the structural similarity.

Where diverged:

The most critical stated divergence is the yield generation mechanism: the new pitch claims yield comes from lending to institutional borrowers and market makers (a real, verifiable counterparty structure used by legitimate CeFi lenders like BlockFi and Celsius), whereas Bitconnect's yield was purportedly generated by an anonymous 'trading bot' with no verifiable basis — later confirmed fraudulent. Additionally, the new pitch is explicitly US-focused consumer (vs. Bitconnect's global footprint), operates in stablecoins as well as BTC (reducing coin-specific volatility risk), and does not describe a multi-level marketing referral structure. The sub_sector tag for Bitconnect is 'cryptocurrency lending scam,' implying intentional fraud; the new pitch presents as a legitimate yield product, though the structural resemblance remains a major regulatory and reputational risk.

Failure causes:

- Ponzi scheme / fraudulent yield mechanism (no real trading bot returns)
- Multi-level marketing structure that attracted regulatory scrutiny
- Unregistered securities offering in US jurisdictions
- Lack of transparency in earnings calculations
- Regulatory cease-and-desist orders from Texas and North Carolina
- Complete collapse of proprietary coin (BCC) liquidity upon shutdown
- Criminal fraud by founders and promoters ($2.4B in investor losses)

Lessons:

- Prove yield generation with audited, third-party-verified documentation of institutional borrower relationships before scaling — opacity about the yield source is the fastest path to a Ponzi allegation.
- Register as a securities offering or obtain appropriate money-transmitter and lending licenses in every US state you operate in before onboarding customers; Bitconnect was shut down specifically for unregistered securities sales in Texas and North Carolina.
- Avoid lock-up + tiered-interest mechanics that structurally resemble high-yield investment programs; consider demand-deposit or shorter-term structures that reduce regulatory surface area.
- Never make the product's coin or token the unit of account for user balances — Bitconnect users lost nearly everything because refunds were denominated in BCC, which crashed 92%; your stablecoin-denominated approach is better but must be maintained under stress.
- Prepare a detailed, public risk-disclosure document explaining counterparty risk, liquidation scenarios, and what happens to user funds if an institutional borrower defaults — regulators and users will demand it, and having it proactively differentiates you from bad actors.

Sources:

https://en.wikipedia.org/wiki/Bitconnect

---

Pipeline meta:

- cost_usd_total: 0.3088
- latency_ms_total: 130936
- trace_id: 92893da9-d833-8ee0-35e3-a8de62d05ccc
- budget_remaining_usd: 1.6912
- budget_exceeded: False
- K_retrieve: 30
- N_synthesize: 5
- min_similarity_score: 4.0

Models:

- facet: anthropic/claude-haiku-4.5
- rerank: anthropic/claude-sonnet-4.6
- synthesize: anthropic/claude-sonnet-4.6