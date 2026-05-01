# Premortem report for (unnamed)

Pitch: We're building a US consumer crypto savings platform that pays high yield on customer deposits by lending them to institutional borrowers and market makers. Web + mobile, no account fees, tiered interest rates by lock-up duration. Already custodying M of customer assets in stablecoins and BTC.

Generated: 2026-05-01T14:27:01.224715+00:00

## Top risks across all comparables

1. [HIGH] Maintain liquidity reserves sufficient for a 20-30% simultaneous withdrawal and stress-test against a 50%+ crypto drawdown.
   Applies because: The platform is 'already custodying M of customer assets' with tiered lock-up durations, creating a direct liquidity mismatch risk if institutional borrowers default or markets crash simultaneously.
   Raised by: Celsius Network, BlockFi, Voyager Digital, FTX (4/4)

2. [HIGH] Obtain legal clarity on whether your yield-bearing deposit product is an unregistered security before onboarding customers.
   Applies because: The pitch describes a 'US consumer crypto savings platform that pays high yield on customer deposits' — exactly the product structure ruled an unregistered security in BlockFi's SEC settlement and Celsius's bankruptcy proceedings.
   Raised by: Celsius Network, BlockFi, FTX (3/4)

3. [HIGH] Enforce hard borrower concentration limits so no single counterparty default can trigger insolvency.
   Applies because: The pitch explicitly relies on 'lending to institutional borrowers and market makers' — concentrated exposure to any single borrower mirrors the 3AC/Voyager and FTX/BlockFi fatal counterparty failures.
   Raised by: BlockFi, Voyager Digital (2/4)

4. [HIGH] Legally segregate customer deposits from lending operations and prove it with third-party audits.
   Applies because: The platform takes 'customer deposits' and lends them out — without segregation and auditable proof-of-reserves, commingling risk is direct and criminal liability follows, as in FTX.
   Raised by: FTX, BlockFi (2/4)

5. [MEDIUM] Never imply FDIC insurance on crypto deposits; get explicit legal guidance before any marketing claim.
   Applies because: This is a 'US consumer crypto savings platform' — the savings framing and deposit language make FDIC misrepresentation a high-probability marketing mistake, as seen with Voyager and FTX.
   Raised by: Voyager Digital, FTX (2/4)

6. [MEDIUM] Pursue qualified third-party custody so no single insider can move customer BTC or stablecoin assets unilaterally.
   Applies because: The pitch states the platform is 'already custodying M of customer assets in stablecoins and BTC' — self-custody without multi-party authorization is the direct self-dealing vector that destroyed FTX.
   Raised by: FTX, Celsius Network (2/4)

7. [MEDIUM] Require over-collateralization and real-time margin calls on all institutional loans; never lend unsecured.
   Applies because: The pitch describes lending customer assets 'to institutional borrowers and market makers' with no mention of collateral requirements — unsecured lending was the proximate cause of Voyager's collapse.
   Raised by: Voyager Digital (1/4)

8. [MEDIUM] Structure consumer lock-up durations to match or exceed the tenor of institutional loans to eliminate liquidity mismatch.
   Applies because: The pitch offers 'tiered interest rates by lock-up duration,' meaning consumer redemption schedules may not align with loan tenors — the exact mismatch that froze Voyager's withdrawals.
   Raised by: Voyager Digital (1/4)

9. [MEDIUM] Never re-hypothecate customer assets beyond a single layer; publish LTV and leverage caps with independent verification.
   Applies because: The platform lends customer stablecoin and BTC deposits to institutional counterparties — uncapped re-hypothecation was a core mechanism of Celsius's collapse.
   Raised by: Celsius Network (1/4)

10. [LOW] Never allow a single strategic investor or credit facility provider to become a dominant counterparty whose failure cascades to yours.
   Applies because: The pitch mentions lending to 'market makers,' a category that could include a single dominant credit relationship — the FTX rescue-turned-death-sentence for BlockFi is the direct analogue.
   Raised by: BlockFi (1/4)

## Celsius Network

Crypto yield platform that paid consumers high interest on deposited digital assets by lending them to institutional borrowers, collapsed in 2022 due to insolvency, fraud, and a bank-run.

Failure date: 2022-01-01
Lifespan: 60 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.5 | Both offer consumers high yield on crypto deposits (including BTC and stablecoins) funded by lending to institutional borrowers/market makers. Both charge no account fees and use tiered or variable rates. The pitch mirrors Celsius's exact revenue mechanism almost point-for-point. |
| market | 9.0 | Both target US consumers seeking yield on crypto holdings. Celsius operated globally but with significant US consumer focus, which attracted US state and federal regulatory scrutiny. The pitch is explicitly US consumer crypto savings — the same primary market Celsius served. |
| gtm | 8.0 | Both use web and mobile apps as distribution. Celsius grew via community-driven yield promises, ICO, and aggressive marketing of superior rates vs. banks. The pitch similarly leads with yield as the primary acquisition hook. Both custodied customer assets directly. |
| stage_scale | 6.0 | The pitch is early-stage ('already custodying M of customer assets'), whereas Celsius at failure had ~$12B AUM and 1.7M customers. Stage differs significantly, though the growth trajectory and scaling ambition are comparable. |

Why similar:

The pitch is structurally near-identical to Celsius: a consumer-facing crypto deposit platform paying yield funded by lending to institutional borrowers and market makers, with no account fees, tiered rates, and custody of BTC and stablecoins via web and mobile. The core value proposition — 'better savings account via crypto lending spread' — is the same product Celsius built and that regulators ultimately deemed an unregistered securities offering.

Where diverged:

1. Geography/regulatory scope: The pitch explicitly targets the US only, whereas Celsius operated globally. A narrower geography could simplify regulatory compliance but also concentrates all exposure to US regulators who were the most aggressive against Celsius. 2. Scale: The pitch is at a very early custodied-assets stage ('M' of assets) vs. Celsius's $12B AUM at peak — the platform has not yet reached systemic size, meaning a failure would be smaller but the path to viability is unproven. 3. No proprietary token: The pitch does not mention a native token (like Celsius's CEL), removing one major vector for market manipulation and Ponzi-like self-dealing that destroyed Celsius. 4. Stablecoin emphasis: The pitch explicitly mentions stablecoins as a primary asset, which may reduce mark-to-market volatility risk compared to Celsius's heavy exposure to volatile crypto assets.

Failure causes:

- excessive re-hypothecation of customer assets amplifying leverage
- bank-run triggered by crypto market downturn (Terra/Luna collapse)
- undisclosed insolvency and $1.2B balance sheet deficit
- CEO fraud and market manipulation of CEL token
- unregistered securities offering in multiple US states
- no deposit insurance leaving customers as unsecured creditors
- opaque and misleading public disclosures during liquidity crisis

Lessons:

- Register or obtain a legal opinion on whether your yield-bearing deposit product constitutes a security under US law before onboarding a single customer.
- Never re-hypothecate customer assets beyond a single layer; establish strict LTV and leverage caps with independent third-party custodians and publish them.
- Maintain a liquidity reserve sufficient to cover a simultaneous withdrawal of at least 20-30% of AUM, and stress-test it against a 50%+ crypto market drawdown.
- Disclose all risks transparently in plain language and never publicly deny a liquidity problem that exists — regulatory and reputational penalties for false assurances are severe and criminal.
- Do not issue a proprietary token tied to platform economics; it creates conflicts of interest, self-dealing incentives, and additional securities law exposure.

Sources:

https://en.wikipedia.org/wiki/Celsius_Network

## BlockFi

US consumer crypto savings and lending platform offering yield on digital asset deposits via institutional lending, ultimately destroyed by concentrated counterparty exposure to FTX.

Failure date: 2022-01-01
Lifespan: 60 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.5 | Both platforms custody consumer crypto/stablecoin deposits and generate yield by lending to institutional borrowers and market makers — identical revenue model, identical product structure (tiered rates, lock-up durations), identical customer-facing value proposition of 'high yield savings.' |
| market | 9.5 | Same geography (US), same customer type (consumer), same sub-sector (cryptocurrency lending), same price point profile (no account fees, interest-rate spread monetization). The Trusted facts confirm US consumer crypto lending as BlockFi's exact market. |
| gtm | 8.0 | Both target retail consumers via web and mobile with a simple savings account metaphor layered on crypto. BlockFi also used this framing heavily. Minor divergence possible in current regulatory environment and channel mix, but the core GTM motion is the same. |
| stage_scale | 7.0 | BlockFi grew to $3B valuation with tens of thousands of creditors before collapse; the new pitch is early-stage with 'M of customer assets.' Stage differs meaningfully, but the operational model and scaling path are identical, making BlockFi a highly relevant warning. |

Why similar:

The new pitch is a near-direct replication of BlockFi's core product: a US consumer-facing digital asset savings account that pays high yield by lending deposits to institutional counterparties, with tiered rates by lock-up duration and no account fees. The Trusted facts confirm alignment on every major taxonomy dimension — geography (US), customer type (consumer), sub-sector (cryptocurrency lending), and monetization model (usage/spread-based). BlockFi's rise and fall is the canonical case study for this exact business.

Where diverged:

1. Stage/scale: The new pitch is early-stage (custodying 'M' of assets) versus BlockFi's $3B peak valuation, meaning the new founder has not yet accumulated the systemic counterparty dependencies that made BlockFi fatally fragile. 2. Timing/regulatory environment: The pitch is being built after BlockFi's collapse and after the SEC/CFTC enforcement wave of 2022–2023, so the regulatory landscape is materially harsher and better-defined — the new founder cannot claim ignorance of the compliance requirements that BlockFi settled for $100M in 2022. 3. Asset composition: The pitch explicitly mentions stablecoins and BTC as custodied assets; BlockFi's exposure was more diversified across volatile assets and included direct FTX/Alameda credit lines, though the pitch does not yet clarify whether it avoids concentrated institutional counterparties.

Failure causes:

- Concentrated counterparty exposure to FTX/Alameda Research
- Contagion from FTX bankruptcy triggering withdrawal halt and insolvency
- $100M SEC/state regulatory settlement for unregistered securities (interest account product)
- $227M in uninsured funds at Silicon Valley Bank at time of bankruptcy
- Insufficient liquidity buffers against simultaneous borrower default and depositor run
- Dependence on a single rescue financier (FTX) that itself collapsed
- Opaque risk disclosures to retail depositors about institutional lending counterparties

Lessons:

- Diversify institutional borrower counterparties aggressively and publish concentration limits publicly — a single borrower's default should not be able to trigger insolvency.
- Engage securities regulators proactively before launch: BlockFi's yield account was ruled an unregistered security; the new founder must obtain legal clarity or a no-action letter before scaling.
- Maintain on-chain, auditable proof-of-reserves and strict liquidity reserves so a simultaneous market shock and depositor run does not force a withdrawal halt.
- Never allow a single strategic investor or credit facility provider to become so large a counterparty that their failure is your failure — the FTX rescue deal became BlockFi's death sentence.
- Keep uninsured cash balances at any single bank below FDIC limits, or use sweep structures, so a bank failure (e.g., SVB) does not add a second simultaneous liquidity crisis.

Sources:

https://en.wikipedia.org/wiki/BlockFi

## Voyager Digital

US consumer cryptocurrency brokerage that offered yield on deposits by lending customer assets to institutional borrowers, collapsed when Three Arrows Capital defaulted on a $666M loan.

Failure date: 2022-01-01
Lifespan: 48 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.0 | Both models custody consumer crypto assets and generate yield by lending those assets to institutional counterparties/market makers. The pitch describes tiered interest rates by lock-up duration, which is functionally identical to Voyager's loyalty-rewards yield product. Core mechanics — take consumer deposits, lend wholesale, pass yield back — are essentially the same. |
| market | 9.0 | Both target US consumers seeking high yield on crypto holdings (stablecoins and BTC explicitly mentioned in the pitch). The addressable market, regulatory environment, and competitive set (CeFi crypto yield) are the same. |
| gtm | 7.0 | Both go direct-to-consumer via web and mobile with no account fees as a primary acquisition hook. Voyager also used commission-free trading as its wedge; the new pitch uses no-fee savings accounts and tiered rates. The channels and incentive structure are closely aligned, though the pitch is more savings-focused than brokerage-focused. |
| stage_scale | 6.0 | The pitch mentions already custodying 'M of customer assets' (presumably millions), suggesting an early but live product — comparable to Voyager's early growth phase before it scaled to ~$5.9B AUM. Both are post-launch with real customer assets but pre-institutional-scale, making the stage reasonably comparable even if absolute AUM differs. |

Why similar:

The new pitch is structurally a near-replica of Voyager Digital's core business: accept consumer crypto deposits in stablecoins and BTC, lend them to institutional borrowers and market makers, and return yield to customers via tiered rate schedules. Both operate in the US, target retail consumers, use a no-fee mobile/web product as the acquisition wedge, and are exposed to the same counterparty-concentration risk that killed Voyager. The regulatory environment (SEC, FDIC, Federal Reserve oversight of crypto yield products) is identical.

Where diverged:

1. Product framing: The pitch is explicitly a 'savings platform' with lock-up tiers, not a brokerage/trading platform — this may reduce transaction-fee revenue dependency but does not eliminate lending risk. 2. Monetization: Voyager's primary monetization was transaction fees from trading; the pitch is silent on transaction fees and implies spread/NIM as the primary revenue source, which is a different P&L profile. 3. Asset composition: The pitch explicitly names stablecoins as a primary custodied asset, which provides some protection against crypto-price volatility on the asset side (though counterparty risk remains). Voyager's collateral mix is not specified in the document. 4. Scale: The pitch is at an earlier absolute AUM stage ('M of assets') versus Voyager at peak ~$5.9B, meaning the new founder has a chance to implement risk controls before reaching systemic exposure levels.

Failure causes:

- Counterparty concentration risk — single borrower (Three Arrows Capital) defaulted on $666M
- Insufficient collateral or credit underwriting on institutional loans
- Regulatory non-compliance — FDIC/Federal Reserve cease-and-desist for misrepresenting deposit insurance
- Contagion from broader crypto market collapse (Terra/LUNA, then FTX)
- Liquidity mismatch — consumer deposits were liquid but loans were not, preventing withdrawals
- Acquisition process delayed by FTX's own bankruptcy, destroying timeline to resolution

Lessons:

- Enforce hard borrower concentration limits — no single counterparty should represent more than 10-15% of the loan book; Three Arrows Capital's default alone was fatal.
- Never imply or state FDIC insurance on crypto deposits; obtain explicit legal guidance before any marketing claim to avoid regulatory cease-and-desist.
- Structure consumer lock-up durations to match or exceed the tenor of institutional loans to eliminate the liquidity mismatch that froze Voyager's withdrawals.
- Require over-collateralization and real-time margin calls on all institutional loans; unsecured or under-collateralized lending to counterparties like 3AC was the proximate cause of failure.
- Build a stress-tested contingency plan for simultaneous borrower default and crypto market drawdown before scaling AUM beyond the current early stage.

Sources:

https://en.wikipedia.org/wiki/Voyager_Digital

## FTX

Cryptocurrency exchange and hedge fund that collapsed in 2022 after executives secretly misappropriated billions in customer deposits to fund related trading firm Alameda Research, triggering a bank run and Chapter 11 bankruptcy.

Failure date: 2022-01-01
Lifespan: 36 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 6.0 | Both platforms take custody of customer crypto assets and deploy them to generate yield — FTX lent customer deposits to Alameda Research; the new pitch explicitly lends deposits to institutional borrowers and market makers. The core mechanism (custody + re-lending) is structurally identical. However, the new pitch monetizes via tiered interest-rate spreads rather than transaction fees, and positions itself as a savings product rather than a trading exchange. |
| market | 7.0 | Both target the consumer crypto market in the US and rely on customer trust in the custody and safety of their digital assets (stablecoins and BTC are explicitly named in the pitch). FTX's peak customer base exceeded one million users, overlapping with the same retail depositor segment the new pitch addresses. FTX also operated FTX.US for US residents specifically. |
| gtm | 4.0 | FTX used aggressive sports sponsorships, celebrity endorsements, and a mass-market brand campaign ('safe, easy way to get into crypto') to acquire users at scale. The new pitch describes a web+mobile product with tiered interest rates — a more product-led, yield-driven acquisition approach with no mention of celebrity marketing or sponsorships. |
| stage_scale | 3.0 | FTX at failure had 1M+ users, $10B daily trading volume, and a $32B valuation. The new pitch is early-stage, custodying an unspecified 'M' of customer assets — orders of magnitude smaller. Stage and scale are materially different. |

Why similar:

Both companies take custody of consumer crypto assets (stablecoins and BTC) and re-deploy those assets to third-party borrowers or trading counterparties to generate returns. The fundamental risk profile — customer deposits lent to undisclosed or loosely governed counterparties — is the same structural vulnerability. Both target US consumers and market themselves on yield and safety. FTX's failure is the canonical example of what happens when this model breaks down.

Where diverged:

1. Product type: the new pitch is a pure savings/yield product with no trading or derivatives functionality; FTX was primarily a trading exchange that also held deposits. 2. Monetization: the new pitch uses tiered interest-rate spreads (subscription_recurring-adjacent), not transaction fees. 3. Scale: the new pitch is early-stage; FTX failed at $32B valuation and $10B daily volume. 4. Fraud dimension: FTX's collapse was driven by deliberate, concealed misappropriation of funds by executives — the new pitch shows no such stated intent, though the structural risk of re-lending customer funds remains. 5. Geography: the new pitch is US-only consumer; FTX was global with a Bahamas domicile.

Failure causes:

- secret misappropriation of customer deposits to related party (Alameda Research)
- complete absence of corporate controls and independent oversight
- balance sheet propped up by illiquid, self-issued collateral (FTT token)
- contagion bank-run triggered by public disclosure of Alameda balance sheet
- regulatory non-compliance and false FDIC insurance representations
- concentration of control in inexperienced, unsupervised leadership
- no segregation between customer custody assets and proprietary trading capital

Lessons:

- Legally segregate customer deposits from any lending or treasury operations and prove it with third-party audits — commingling funds is the single most direct path to criminal liability and collapse.
- Disclose to customers exactly who the institutional borrowers and market makers are, what collateral backs those loans, and what haircuts apply — opacity about counterparty risk is what triggered FTX's bank run.
- Ensure every yield-bearing product is reviewed by US securities and banking counsel before launch; FTX was hit with a cease-and-desist for misrepresenting FDIC coverage, a mistake a savings-platform pitch is especially prone to repeating.
- Model a worst-case simultaneous withdrawal scenario and maintain liquid reserves sufficient to meet it — FTX had $900M liquid against $9B in liabilities; a crypto savings platform must prove solvency before it scales.
- Pursue independent custody arrangements (e.g., qualified custodian under the Investment Advisers Act) rather than self-custody so that no single insider can move customer assets without multi-party authorization.

Sources:

https://en.wikipedia.org/wiki/FTX

---

Pipeline meta:

- cost_usd_total: 0.1430
- latency_ms_total: 104350
- trace_id: 55aecdaf-9ef2-5d49-83ca-cd7192da5a7c
- budget_remaining_usd: 1.8570
- budget_exceeded: False
- K_retrieve: 30
- N_synthesize: 5
- min_similarity_score: 4.0

Models:

- facet: anthropic/claude-haiku-4.5
- rerank: anthropic/claude-sonnet-4.6
- synthesize: anthropic/claude-sonnet-4.6
