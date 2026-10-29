# Corpus sources (all public, text layer present: no OCR needed)

| File stem | Document | URL | SHA-256 (first 12) | PDF pages |
|---|---|---|---|---|
| `cdsco_rules` | New Drugs and Clinical Trials Rules, 2019 (incl. Fifth Schedule, PSUR timelines) | https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2022/new_DC_rules/New%20Drugs%20and%20Clinical%20Trials%20Rules%2C%202019.pdf | b25fdd5476a2 | 264 |
| `ipc_mah_v2` | IPC Pharmacovigilance Guidance Document for Marketing Authorisation Holders (Sept 2024) | https://www.ipc.gov.in/images/Guidance_Document_-_17.9.2024_5_mb.pdf | 9142de7460f7 | 86 |
| `tegretol_us_pi` | Tegretol (carbamazepine) prescribing information, Novartis | https://www.novartis.com/us-en/sites/novartis_us/files/tegretol.pdf | 7e788668f892 | 25 |

**Drug choice.** Carbamazepine: its label carries a boxed warning for serious dermatologic reactions (SJS/TEN), which makes the section 4.1 case ("skin peeling ... admitted to hospital") a *listed, serious* reaction. **Limitation to state in the report:** this is the US label, not an Indian-market insert; expectedness in India is judged against the CDSCO-approved product information. Swapping in an Indian insert needs only a new PDF in `corpus/source_pdfs/` and an entry in `corpus/drugs.json`.

**WHO-UMC causality criteria** are not indexed: this system assesses seriousness, expectedness and reporting route, and deliberately makes no causality claim (the output filter blocks "causality is certain/proven").

**Synthetic data only.** No real patient or case data enters the system at any point.
