# Premortem report for (unnamed)

Pitch: We're building a US consumer crypto savings platform that pays high yield on customer deposits by lending them to institutional borrowers and market makers. Web + mobile, no account fees, tiered interest rates by lock-up duration. Already custodying
  ~$50M of customer assets in stablecoins and BTC.

Generated: 2026-05-01T12:56:34.885692+00:00

## Celsius Network

Crypto yield-bearing deposit and lending platform that collapsed after re-hypothecating customer assets into illiquid positions, triggering a bank run and $1.2B balance-sheet deficit.

Failure date: 2022-01-01
Lifespan: 60 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.0 | Near-identical model: consumers deposit crypto assets (BTC, stablecoins), earn tiered yield funded by lending to institutional borrowers and market makers, no account fees, web + mobile product. The pitch mirrors Celsius's core mechanic almost exactly. |
| market | 9.0 | Same customer type (retail/consumer), same sub-sector (cryptocurrency lending/savings), overlapping asset classes (BTC, stablecoins). Celsius served a global audience but had a heavy US consumer base; the new pitch explicitly targets US consumers. |
| gtm | 7.0 | Both rely on high advertised yield as the primary customer acquisition hook. Celsius used a mobile-first app, ICO token incentives, and aggressive social media promotion by its CEO; the new pitch focuses on web + mobile without a native token, which is a partial divergence. |
| stage_scale | 6.0 | The new pitch is early-stage (~$50M AUM); Celsius at comparable AUM stages (2018-2019) was structurally similar. Celsius ultimately reached ~$12B AUM before collapse. The new founder is at a much earlier scale, which is both a risk and an opportunity to course-correct. |

Why similar:

The new pitch replicates Celsius's foundational architecture: accept consumer crypto deposits, pay tiered yield by duration, fund payouts through institutional lending and market-making relationships, charge no fees, and offer both BTC and stablecoin custody. These are the exact mechanisms that defined — and ultimately destroyed — Celsius. The product surface (web + mobile), the customer type (US retail), and the monetization logic (spread between institutional borrowing cost and consumer deposit yield) are structurally identical.

Where diverged:

1. No native token: The pitch does not mention a proprietary token (like Celsius's CEL), removing one lever of yield manipulation and Ponzi-like self-dealing but also one fundraising tool. 2. Geography focus: The pitch explicitly targets US consumers, whereas Celsius operated globally; this concentrates regulatory exposure but may allow tighter compliance design. 3. Scale: $50M AUM at pitch vs. Celsius's $12B peak — the new founder has not yet grown into the leverage that made Celsius catastrophic, but the model's fragility is present from day one. 4. Lock-up tiers: The pitch emphasizes tiered interest by lock-up duration as an explicit product feature; Celsius's lock-up terms were less prominently structured as a core differentiator at launch.

Failure causes:

- excessive asset re-hypothecation creating hidden leverage
- bank run triggered by illiquid deployment of customer deposits
- absence of deposit insurance or regulatory backstop
- unregistered securities offering in multiple US states
- CEO fraud, market manipulation of native CEL token, and insider self-dealing
- yield promises (up to 17%) that required unsustainable risk-taking to honour
- no ring-fencing of customer assets from proprietary trading losses

Lessons:

- Never re-hypothecate customer deposits beyond a single, fully collateralised lending layer — Prime Trust's warning that 'lending the same assets over and over' would be 'destined for failure' proved prophetic.
- Register interest-bearing accounts as securities or obtain explicit state-by-state exemptions before launch; multiple US states issued cease-and-desist orders against Celsius for exactly this product.
- Maintain a liquid reserve buffer large enough to honour withdrawals during a crypto market drawdown of 50%+ — Celsius's $167M cash against $4.7B in user liabilities was catastrophically insufficient.
- Avoid issuing a native token whose price props up your yield obligations; Celsius spent $350M buying its own CEL token to pay interest, masking insolvency.
- Build transparent, audited proof-of-reserves from day one; Celsius's opacity about asset deployment destroyed trust the moment market conditions tightened.

Sources:



## BlockFi

US consumer crypto lending platform offering yield on digital asset deposits by on-lending to institutional borrowers — collapsed in Nov 2022 as FTX contagion destroyed its balance sheet and a $100M SEC/state settlement had already weakened it.

Failure date: 2022-01-01
Lifespan: 60 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.5 | Both platforms take consumer crypto deposits, pay tiered yield, and generate spread by lending assets to institutional counterparties/market makers — nearly identical liability-funded lending model with no account fees. |
| market | 9.5 | Same geography (US), same customer type (consumer), same sub-sector (cryptocurrency lending), same asset classes (stablecoins and BTC), same competitive set of crypto yield platforms. |
| gtm | 8.5 | Both rely on web + mobile self-service acquisition targeting retail crypto holders seeking yield above exchange rates; BlockFi similarly led with ease-of-use and a referral/interest-rate marketing angle rather than enterprise sales. |
| stage_scale | 7.0 | The new pitch already custodies ~$50M in assets, which is comparable to early-stage BlockFi; BlockFi reached $3B valuation and billions in AUM before collapse, so the candidate is earlier in scale but on the same trajectory arc. |

Why similar:

The pitch is functionally a re-run of BlockFi's core product: a US consumer-facing platform that custodies crypto assets (stablecoins + BTC), pays tiered interest by lock-up duration, and earns a spread by on-lending to institutional borrowers and market makers. Business model, customer type, geography, monetization mechanism, and asset classes are essentially identical.

Where diverged:

The only dimension where divergence could exist is timing and regulatory posture — BlockFi operated from 2017 through 2022 and was caught without a registered securities product, resulting in a $100M SEC/state settlement in early 2022 before FTX even collapsed. The new pitch does not describe its regulatory structure (registered vs. unregistered interest accounts), counterparty collateralisation rules, or custody/insurance arrangements, so it is unclear whether those gaps have been addressed. The pitch's ~$50M AUM figure also places it at a much smaller scale than BlockFi at peak, meaning counterparty concentration risk could be proportionally larger.

Failure causes:

- Counterparty concentration in FTX / Alameda Research
- Unregistered securities products triggering $100M SEC and multi-state settlement
- Inadequate collateralisation and liquidity buffers against institutional borrower defaults
- $227M uninsured cash held at Silicon Valley Bank adding secondary loss
- Contagion from Three Arrows Capital default preceding FTX collapse
- Dependency on a single rescue acquirer (FTX) that itself became insolvent

Lessons:

- Diversify institutional borrower exposure and require over-collateralisation with real-time margin calls — single-counterparty concentration killed BlockFi.
- Register yield-bearing accounts with the SEC and relevant state regulators before scaling; BlockFi's $100M settlement weakened it structurally before any market shock hit.
- Maintain liquid reserves sufficient to honour withdrawals during a 30-day contagion event; BlockFi had to halt withdrawals immediately when FTX froze.
- Keep uninsured cash at multiple FDIC-insured institutions and diversify banking relationships to avoid a second-order loss channel (cf. BlockFi's $227M at SVB).
- Avoid taking emergency rescue capital from a single crypto exchange counterparty; that dependency turned BlockFi's lifeline into a fatal liability.

Sources:



## Voyager Digital

US crypto brokerage that paid yield on customer deposits by lending to institutional counterparties, ultimately failing when Three Arrows Capital defaulted on a $666M loan.

Failure date: 2022-01-01
Lifespan: 48 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 9.0 | Both models custody consumer crypto assets, pay yield funded by lending those assets to institutional borrowers/market makers, and operate in the US consumer market. The pitch's tiered lock-up structure is a minor variation on the same core mechanic Voyager used. |
| market | 9.0 | Identical market: US retail/consumer crypto savers seeking yield on stablecoins and BTC. Same regulatory jurisdiction and same competitive set (CeFi yield platforms). |
| gtm | 7.0 | Both target US consumers via web and mobile with no account fees and interest-rate incentives. Voyager also listed on the Toronto Stock Exchange and had a broader brokerage offering, but the consumer acquisition motion is nearly identical. |
| stage_scale | 6.0 | The pitch is already custodying ~$50M of assets, placing it at an early-traction stage. Voyager at failure was custodying over $1.3B. Both are post-launch with real AUM but the pitch is materially smaller, giving it somewhat more flexibility but also less negotiating power with institutional borrowers. |

Why similar:

The new pitch is structurally a near-clone of Voyager Digital's core business: accept US consumer deposits in crypto (stablecoins, BTC), custody them, and generate yield by deploying capital to institutional borrowers and market makers. Both use a tiered interest/lock-up model, both target retail users via web and mobile, both rely on institutional counterparty relationships to fund the yield promise, and both operate in the same US regulatory environment that ultimately scrutinized Voyager for misleading FDIC/banking insurance claims.

Where diverged:

1. Lock-up structure: the new pitch explicitly ties yield to lock-up duration, which gives some liquidity mismatch protection that Voyager lacked. 2. Asset focus: the pitch emphasizes stablecoins alongside BTC, potentially reducing mark-to-market volatility on the asset side. 3. Scale: the pitch is at ~$50M AUM versus Voyager's $1.3B+ at failure — earlier stage means less systemic exposure but also thinner counterparty diversification. 4. Monetization label: Trusted facts tag Voyager primarily as transaction_fee revenue; the pitch describes no trading fees, relying on the interest-rate spread, which is a slightly different P&L structure.

Failure causes:

- Concentrated institutional counterparty risk (Three Arrows Capital default on $666M loan)
- Insufficient due diligence on borrower solvency
- Liquidity mismatch between customer withdrawal rights and illiquid loan book
- Misleading consumer communications about FDIC insurance coverage
- Regulatory cease-and-desist from Federal Reserve and FDIC
- Contagion exposure to FTX collapse disrupting acquisition process
- ~$1.3B customer assets frozen with limited recovery

Lessons:

- Diversify institutional borrowers aggressively — never allow a single counterparty to represent more than a small fraction of total deployed capital.
- Implement transparent, legally reviewed disclosures about deposit insurance: crypto custodied assets are NOT FDIC-insured and regulators will act if this is misrepresented.
- Match lock-up terms on the liability side (customer deposits) to the actual duration of loans on the asset side to prevent a run-driven liquidity crisis.
- Build a liquidation and wind-down protocol before you need it — Voyager's bankruptcy froze $270M+ in customer cash for months.
- Stress-test institutional borrower concentration: if your largest borrower defaults, model whether you can still honor all customer withdrawals without bankruptcy.

Sources:



## Bitconnect

A crypto lending platform that promised high daily interest yields via a proprietary trading bot, later revealed to be a $2.4B Ponzi scheme that collapsed under regulatory cease-and-desist orders in January 2018.

Failure date: 2018-01-01
Lifespan: 24 months

Similarity:

| Perspective | Score | Rationale |
| --- | --- | --- |
| business_model | 7.0 | Both platforms accept consumer crypto deposits, lock them for set durations, and promise yield generated from lending/trading activity. The surface structure — deposit crypto, earn tiered interest by lock-up period — is nearly identical. The critical difference is that Bitconnect's yield was fraudulent (Ponzi-funded), while the new pitch claims legitimate institutional lending. The structural resemblance, however, is high and regulators will notice it immediately. |
| market | 8.0 | Both target retail/consumer crypto holders in overlapping geographies (Bitconnect was global including the US; the new pitch is US-focused). Both operate in the same sub-sector: crypto yield/savings products pitched to consumers. The macro market conditions differ (2016-2018 vs. present), but the customer segment and asset class are essentially the same. |
| gtm | 4.0 | Bitconnect relied heavily on multilevel marketing (MLM) referral chains and influencer promoters. The new pitch describes a web+mobile savings platform with no mention of MLM or referral schemes. GTM approach diverges significantly, though both ultimately depend on consumer trust and word-of-mouth in a skeptical regulatory environment. |
| stage_scale | 6.0 | Bitconnect scaled to hundreds of millions in deposited value before collapse. The new pitch reports ~$50M AUC, placing it at a comparable early-growth stage. Both had achieved real AUC traction at time of crisis, making regulatory scrutiny the dominant near-term risk for both. |

Why similar:

Both are consumer-facing crypto yield platforms that accept deposits (BTC and/or stablecoins), lock them for defined periods, and promise interest returns derived from lending or trading activity. The pitch's tiered lock-up structure mirrors Bitconnect's core mechanic almost exactly. Both had achieved material AUC before facing existential regulatory pressure. Regulators explicitly cited Bitconnect for unregistered securities sales — the same framing will be applied to any platform paying yield on crypto deposits to US retail investors.

Where diverged:

1. Legitimacy of yield source: The new pitch claims yield comes from lending to real institutional borrowers and market makers, not a fabricated 'trading bot' — this is the single most important divergence, but it must be proven to regulators with auditable proof-of-loans. 2. Organizational transparency: Bitconnect operated anonymously with no registered legal entity; the new pitch implies a real, incorporated US company. 3. MLM distribution: Bitconnect used aggressive referral pyramid promoters; the new pitch does not mention this model. 4. Geography: The new pitch is explicitly US-focused, meaning US regulatory frameworks (SEC, state securities regulators) apply from day one rather than as a surprise. 5. Asset mix: The new pitch holds stablecoins as well as BTC, reducing (but not eliminating) price-collapse risk on collateral.

Failure causes:

- Fraudulent yield source (Ponzi scheme — no real trading bot profits)
- Multilevel marketing structure flagged as securities violation
- Failure to register as a securities dealer in US states
- Opaque and anonymous corporate structure with no legal entity
- Regulatory cease-and-desist orders from Texas and North Carolina forced immediate shutdown
- 92% collateral price collapse once confidence broke
- Criminal fraud by founders and promoters leading to DOJ indictment and SEC civil action

Lessons:

- Register with the SEC and relevant state securities regulators before accepting US consumer deposits — the SEC sued Bitconnect for exactly the unregistered securities offering you are replicating in structure.
- Publish third-party audited proof of institutional loan books and borrower creditworthiness; any 'black box' yield mechanism will be compared to Bitconnect's trading bot and presumed fraudulent by regulators and press.
- Segregate customer assets in a qualified custodian and make custody arrangements publicly verifiable — Bitconnect's asset freeze revealed it had no identifiable legal entity or segregated assets.
- Avoid any referral, affiliate, or tiered-reward promotional structure; even if your yield is real, MLM mechanics are the fastest path to a Ponzi-scheme characterization by regulators.
- Engage securities counsel to determine whether your tiered yield product constitutes an investment contract under Howey before launch — state regulators will issue cease-and-desist orders if you have not addressed this proactively.

Sources:



---

Pipeline meta:

- cost_usd_total: 0.1414
- latency_ms_total: 102863
- trace_id: 30087b69-d301-1d89-21a0-25220d945779
- budget_remaining_usd: 1.8586
- budget_exceeded: False
- K_retrieve: 30
- N_synthesize: 5
- min_similarity_score: 4.0

Models:

- facet: anthropic/claude-haiku-4.5
- rerank: anthropic/claude-sonnet-4.6
- synthesize: anthropic/claude-sonnet-4.6
