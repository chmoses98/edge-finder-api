# MLB market registry

As of `catalogue 2026-10-26 + archive through newest observation`. Series: 218 (MIXED_COMBO 1, MLB 189, MLB_EXHIBITION 3, NOT_MLB 25).

## Game-level MLB series (priced from game state)

| Series | Family | Scope | Rung | Markets seen | Games | Settled | Settled games | Model | Gaps |
|---|---|---|---|---:|---:|---:|---:|---|---|
| KXMLBDELAY | game_specials | None | CLASSIFIED | 0 | 0 | 0 | 0 | NO_MODEL | NO_MARKET_ROWS_OBSERVED: parse not evidenced; NO_MODEL_PATH (NO_MODEL); SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION; NOT_CAPTURED_BY_ALPHA0002; NOT_CAPTURED_BY_MRV |
| KXMLBERA | pitcher_earned_runs | FULL_GAME | PARSEABLE | 2696 | 360 | 0 | 0 | NO_MODEL | NO_MODEL_PATH (NO_MODEL); SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION |
| KXMLBEXTRAS | extra_innings | FULL_GAME | PRICEABLE | 379 | 379 | 0 | 0 | PRICED_BY_UNIFIED_SIM_APPROXIMATE | SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION |
| KXMLBF3 | period_result | F3 | SAMPLE_FLOOR_MET | 2442 | 814 | 2355 | 782 | PRICED_BY_UNIFIED_SIM_APPROXIMATE | - |
| KXMLBF5 | period_result | F5 | SAMPLE_FLOOR_MET | 2445 | 815 | 2352 | 783 | PRICED_BY_UNIFIED_SIM_APPROXIMATE | - |
| KXMLBF5SPREAD | period_margin | F5 | SAMPLE_FLOOR_MET | 3260 | 815 | 3140 | 783 | PRICED_BY_UNIFIED_SIM_APPROXIMATE | - |
| KXMLBF5TOTAL | period_total | F5 | SAMPLE_FLOOR_MET | 5705 | 815 | 5502 | 784 | PRICED_BY_UNIFIED_SIM_APPROXIMATE | - |
| KXMLBF7 | period_result | F7 | SAMPLE_FLOOR_MET | 2448 | 816 | 2352 | 784 | PRICED_BY_UNIFIED_SIM_APPROXIMATE | - |
| KXMLBGAME | game_result | FULL_GAME | SAMPLE_FLOOR_MET | 1634 | 817 | 1558 | 779 | PRICED_BY_UNIFIED_SIM | - |
| KXMLBHA | pitcher_hits_allowed | FULL_GAME | PARSEABLE | 3323 | 359 | 0 | 0 | NO_MODEL | NO_MODEL_PATH (NO_MODEL); SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION |
| KXMLBHIT | hitter_hits | FULL_GAME | SAMPLE_FLOOR_MET | 44815 | 814 | 41369 | 777 | EXISTING_PLAYER_MODEL_NOT_WIRED | - |
| KXMLBHR | hitter_home_runs | FULL_GAME | PARSEABLE | 12577 | 382 | 0 | 0 | NO_MODEL | NO_MODEL_PATH (NO_MODEL); SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION |
| KXMLBHRR | hitter_hits_runs_rbis | FULL_GAME | SAMPLE_FLOOR_MET | 66620 | 811 | 61881 | 771 | EXISTING_PLAYER_MODEL_NOT_WIRED | - |
| KXMLBINNINGTOTAL | inning_total | INNING_N | PRICEABLE | 6491 | 383 | 0 | 0 | PRICED_BY_UNIFIED_SIM_APPROXIMATE | SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION |
| KXMLBINNINGWIN | inning_winner | INNING_N | PRICEABLE | 10305 | 383 | 0 | 0 | PRICED_BY_UNIFIED_SIM_APPROXIMATE | SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION |
| KXMLBKS | pitcher_strikeouts | FULL_GAME | SAMPLE_FLOOR_MET | 11150 | 804 | 10512 | 770 | EXISTING_PLAYER_MODEL_NOT_WIRED | - |
| KXMLBOUTS | pitcher_outs | FULL_GAME | SAMPLE_FLOOR_MET | 1512 | 801 | 1428 | 769 | EXISTING_PLAYER_MODEL_NOT_WIRED | - |
| KXMLBRBI | hitter_rbis | FULL_GAME | SAMPLE_FLOOR_MET | 28767 | 814 | 26615 | 776 | EXISTING_PLAYER_MODEL_NOT_WIRED | - |
| KXMLBRFI | first_inning_run | INNING_1 | SAMPLE_FLOOR_MET | 816 | 816 | 785 | 780 | PRICED_BY_UNIFIED_SIM_APPROXIMATE | - |
| KXMLBSB | hitter_stolen_bases | FULL_GAME | SAMPLE_FLOOR_MET | 11722 | 785 | 10762 | 740 | EXISTING_PLAYER_MODEL_NOT_WIRED | - |
| KXMLBSPREAD | winning_margin | FULL_GAME | SAMPLE_FLOOR_MET | 6144 | 816 | 5730 | 784 | PRICED_BY_UNIFIED_SIM | - |
| KXMLBSTART | game_specials | None | CLASSIFIED | 0 | 0 | 0 | 0 | NO_MODEL | NO_MARKET_ROWS_OBSERVED: parse not evidenced; NO_MODEL_PATH (NO_MODEL); SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION; NOT_CAPTURED_BY_ALPHA0002; NOT_CAPTURED_BY_MRV |
| KXMLBSTARTER | game_specials | None | CLASSIFIED | 0 | 0 | 0 | 0 | NO_MODEL | NO_MARKET_ROWS_OBSERVED: parse not evidenced; NO_MODEL_PATH (NO_MODEL); SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION; NOT_CAPTURED_BY_ALPHA0002; NOT_CAPTURED_BY_MRV |
| KXMLBSTARTINGLINEUP | game_specials | None | CLASSIFIED | 0 | 0 | 0 | 0 | NO_MODEL | NO_MARKET_ROWS_OBSERVED: parse not evidenced; NO_MODEL_PATH (NO_MODEL); SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION; NOT_CAPTURED_BY_ALPHA0002; NOT_CAPTURED_BY_MRV |
| KXMLBTB | hitter_total_bases | FULL_GAME | SAMPLE_FLOOR_MET | 53691 | 812 | 49555 | 772 | EXISTING_PLAYER_MODEL_NOT_WIRED | - |
| KXMLBTEAMTOTAL | team_total | FULL_GAME | SAMPLE_FLOOR_MET | 11424 | 816 | 10976 | 784 | PRICED_BY_UNIFIED_SIM | - |
| KXMLBTOTAL | game_total | FULL_GAME | SAMPLE_FLOOR_MET | 9501 | 816 | 9052 | 784 | PRICED_BY_UNIFIED_SIM | - |
| KXMLBWA | pitcher_walks_allowed | FULL_GAME | PARSEABLE | 1761 | 357 | 0 | 0 | NO_MODEL | NO_MODEL_PATH (NO_MODEL); SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION |
| KXMLBWALK | hitter_walks | FULL_GAME | CLASSIFIED | 0 | 0 | 0 | 0 | NO_MODEL | NO_MARKET_ROWS_OBSERVED: parse not evidenced; NO_MODEL_PATH (NO_MODEL); SETTLEMENT_TRUTH_MISSING (archived settled rows: 0); NOT_CAPTURED_BY_PRODUCTION |

## Excluded: not MLB or not pooled

| Series | Verdict | Evidence |
|---|---|---|
| KXATTENDMLB | NOT_MLB | celebrity attendance market, no MLB game outcome |
| KXCONGRESSBASEBALL | NOT_MLB | Congressional Baseball Game (novelty) |
| KXESPYMLB | NOT_MLB | ESPY award market, not an MLB-administered outcome |
| KXLMBGAME | NOT_MLB | Mexican Baseball League (LMB) |
| KXMEDIACOVERMLBTHESHOW | NOT_MLB | video-game cover athlete market |
| KXMLBASGAME | MLB_EXHIBITION | All-Star Game exhibition |
| KXMLBBGAME | NOT_MLB | Mobile Legends: Bang Bang esports (title 'Mobile Legends Bang Bang Game'); the KXMLB prefix is a coincidence |
| KXMLBFTGAME | MLB_EXHIBITION | Futures Game (prospects exhibition) |
| KXMLBSTGAME | MLB_EXHIBITION | spring-training exhibition games |
| KXMLBWORLD | NOT_MLB | World Baseball Classic (national teams), not MLB |
| KXNCAABASEBALL | NOT_MLB | college baseball |
| KXNCAABBCONF | NOT_MLB | college baseball |
| KXNCAABBFINAL | NOT_MLB | college baseball |
| KXNCAABBGAME | NOT_MLB | college baseball |
| KXNCAABBGS | NOT_MLB | college baseball |
| KXNCAABBHR | NOT_MLB | college baseball |
| KXNCAABBPLAYOFFS | NOT_MLB | college baseball |
| KXNCAABBSPREAD | NOT_MLB | college baseball |
| KXNCAABBTOTAL | NOT_MLB | college baseball |
| KXNCAAMBACHAMP | NOT_MLB | college baseball |
| KXTEAMSINNCAABBWS | NOT_MLB | college baseball |
| KXWBCGAME | NOT_MLB | World Baseball Classic (national teams) |
| KXWBCHIT | NOT_MLB | World Baseball Classic (national teams) |
| KXWBCHR | NOT_MLB | World Baseball Classic (national teams) |
| KXWBCKS | NOT_MLB | World Baseball Classic (national teams) |
| KXWBCRFI | NOT_MLB | World Baseball Classic (national teams) |
| KXWBCSPREAD | NOT_MLB | World Baseball Classic (national teams) |
| KXWBCTOTAL | NOT_MLB | World Baseball Classic (national teams) |

## MLB catalogue without a game path (futures, awards, series, events)

| Series | Domain | Family |
|---|---|---|
| KXCITYMLBEXPAND | CORPORATE | league_business_event |
| KXCOACHOUTMLB | EVENT | league_news_event |
| KXCOACHOUTMLBDATE | EVENT | league_news_event |
| KXLEADERMLBAVG | LEADER | season_leader |
| KXLEADERMLBDOUBLES | LEADER | season_leader |
| KXLEADERMLBERA | LEADER | season_leader |
| KXLEADERMLBHITS | LEADER | season_leader |
| KXLEADERMLBHR | LEADER | season_leader |
| KXLEADERMLBKS | LEADER | season_leader |
| KXLEADERMLBOPS | LEADER | season_leader |
| KXLEADERMLBRBI | LEADER | season_leader |
| KXLEADERMLBRUNS | LEADER | season_leader |
| KXLEADERMLBSAVES | LEADER | season_leader |
| KXLEADERMLBSTEALS | LEADER | season_leader |
| KXLEADERMLBSTRIKEOUTS | LEADER | season_leader |
| KXLEADERMLBTRIPLES | LEADER | season_leader |
| KXLEADERMLBWAR | LEADER | season_leader |
| KXLEADERMLBWINS | LEADER | season_leader |
| KXMLB | SEASON_FUTURE | pennant_world_series_future |
| KXMLB100LOSS | SEASON_FUTURE | season_structure_future |
| KXMLB500 | SEASON_FUTURE | season_structure_future |
| KXMLBAL | SEASON_FUTURE | pennant_world_series_future |
| KXMLBALCENT | SEASON_FUTURE | division_future |
| KXMLBALCPOTY | AWARD | season_award |
| KXMLBALCSMVP | AWARD | postseason_mvp |
| KXMLBALCSQUAL | SEASON_FUTURE | season_structure_future |
| KXMLBALCY | AWARD | season_award |
| KXMLBALEAST | SEASON_FUTURE | division_future |
| KXMLBALHAARON | AWARD | season_award |
| KXMLBALLSTAR | EVENT | all_star_event |
| KXMLBALLSTARHR | EVENT | all_star_event |
| KXMLBALMOTY | AWARD | season_award |
| KXMLBALMVP | AWARD | season_award |
| KXMLBALRELOTY | AWARD | season_award |
| KXMLBALROTY | AWARD | season_award |
| KXMLBALWEST | SEASON_FUTURE | division_future |
| KXMLBASGHIT | EVENT | all_star_event |
| KXMLBASGHR | EVENT | all_star_event |
| KXMLBASGKS | EVENT | all_star_event |
| KXMLBASGMVP | EVENT | all_star_event |
| KXMLBAWARDCOMBO | AWARD | season_award |
| KXMLBAWARDFIN | AWARD | season_award |
| KXMLBBESTRECORD | SEASON_FUTURE | season_structure_future |
| KXMLBCBA | CORPORATE | league_business_event |
| KXMLBCHAMPDIVISION | SEASON_FUTURE | division_future |
| KXMLBCHAMPLEAGUE | SEASON_FUTURE | division_future |
| KXMLBCOACH | EVENT | league_news_event |
| KXMLBDEBUT | EVENT | league_news_event |
| KXMLBDIVWINNER | SEASON_FUTURE | division_future |
| KXMLBDRAFTPICK | CORPORATE | league_business_event |
| KXMLBDRAFTTOP | CORPORATE | league_business_event |
| KXMLBEOTY | AWARD | season_award |
| KXMLBFASTPITCH | SEASON_FUTURE | season_stat_future |
| KXMLBFOD | EVENT | league_news_event |
| KXMLBFODTEAMS | EVENT | league_news_event |
| KXMLBGG | AWARD | season_award |
| KXMLBHRDERBY | EVENT | all_star_event |
| KXMLBHRDERBY500 | EVENT | all_star_event |
| KXMLBHRDERBYDISTANCE | EVENT | all_star_event |
| KXMLBHRDERBYFIN | EVENT | all_star_event |
| KXMLBHRDERBYFORECAST | EVENT | all_star_event |
| KXMLBHRDERBYLONGEST | EVENT | all_star_event |
| KXMLBHRDERBYMATCHUP | EVENT | all_star_event |
| KXMLBHRDERBYOU | EVENT | all_star_event |
| KXMLBHRDERBYQUAL | EVENT | all_star_event |
| KXMLBHRDERBYR1LEAD | EVENT | all_star_event |
| KXMLBHRDERBYSEMI | EVENT | all_star_event |
| KXMLBHRDERBYTOT | EVENT | all_star_event |
| KXMLBHRDERBYVELO | EVENT | all_star_event |
| KXMLBLEADERPLAYOFF | SEASON_FUTURE | season_structure_future |
| KXMLBLSTREAK | SEASON_FUTURE | season_stat_future |
| KXMLBMATCHUP | SEASON_FUTURE | season_structure_future |
| KXMLBMENTION | EVENT | league_news_event |
| KXMLBNEXTHR | EVENT | league_news_event |
| KXMLBNEXTTEAM | EVENT | league_news_event |
| KXMLBNL | SEASON_FUTURE | pennant_world_series_future |
| KXMLBNLCENT | SEASON_FUTURE | division_future |
| KXMLBNLCPOTY | AWARD | season_award |
| KXMLBNLCSMVP | AWARD | postseason_mvp |
| KXMLBNLCSQUAL | SEASON_FUTURE | season_structure_future |
| KXMLBNLCY | AWARD | season_award |
| KXMLBNLEAST | SEASON_FUTURE | division_future |
| KXMLBNLHAARON | AWARD | season_award |
| KXMLBNLMOTY | AWARD | season_award |
| KXMLBNLMVP | AWARD | season_award |
| KXMLBNLRELOTY | AWARD | season_award |
| KXMLBNLROTY | AWARD | season_award |
| KXMLBNLWEST | SEASON_FUTURE | division_future |
| KXMLBOAK | CORPORATE | league_business_event |
| KXMLBOPENINGDAY | EVENT | league_news_event |
| KXMLBPITCH | EVENT | player_pitching_event |
| KXMLBPITCHEROTM | AWARD | monthly_weekly_award |
| KXMLBPLAYEROTM | AWARD | monthly_weekly_award |
| KXMLBPLAYEROTW | AWARD | monthly_weekly_award |
| KXMLBPLAYOFFHOST | SEASON_FUTURE | season_structure_future |
| KXMLBPLAYOFFHRS | SEASON_FUTURE | season_structure_future |
| KXMLBPLAYOFFS | SEASON_FUTURE | season_structure_future |
| KXMLBRETURN | EVENT | league_news_event |
| KXMLBSEASONGAMES | SEASON_FUTURE | season_stat_future |
| KXMLBSEASONHR | SEASON_FUTURE | season_stat_future |
| KXMLBSEEDWIN | SEASON_FUTURE | season_structure_future |
| KXMLBSERIES | SERIES | playoff_series_winner |
| KXMLBSERIESEXACT | SERIES | playoff_series_prop |
| KXMLBSERIESGAMES | SERIES | playoff_series_prop |
| KXMLBSERIESGAMETOTAL | SERIES | playoff_series_prop |
| KXMLBSERIESHR | SERIES | playoff_series_prop |
| KXMLBSERIESSCORE | SERIES | playoff_series_prop |
| KXMLBSERIESSPREAD | SERIES | playoff_series_prop |
| KXMLBSISTREAK | SEASON_FUTURE | season_stat_future |
| KXMLBSS | AWARD | season_award |
| KXMLBSTAT | SEASON_FUTURE | season_stat_future |
| KXMLBSTATCOUNT | SEASON_FUTURE | season_stat_future |
| KXMLBSTRIKE | CORPORATE | league_business_event |
| KXMLBTEAMSALE | CORPORATE | league_business_event |
| KXMLBTEAMSTAT | SEASON_FUTURE | season_stat_future |
| KXMLBTRADE | CORPORATE | league_business_event |
| KXMLBTRIPLECROWN | SEASON_FUTURE | season_stat_future |
| KXMLBWALKOFFS | SEASON_FUTURE | season_stat_future |
| KXMLBWINS | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-ATH | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-ATL | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-AZ | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-BAL | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-BOS | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-CHC | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-CIN | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-CLE | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-COL | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-CWS | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-DET | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-HOU | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-KC | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-LAA | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-LAD | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-MIA | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-MIL | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-MIN | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-NYM | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-NYY | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-PHI | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-PIT | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-SD | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-SEA | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-SF | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-STL | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-TB | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-TEX | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-TOR | SEASON_FUTURE | team_season_wins |
| KXMLBWINS-WSH | SEASON_FUTURE | team_season_wins |
| KXMLBWORSTRECORD | SEASON_FUTURE | season_structure_future |
| KXMLBWS | SEASON_FUTURE | pennant_world_series_future |
| KXMLBWSMVP | SERIES | world_series_outcome |
| KXMLBWSOUTCOME | SERIES | world_series_outcome |
| KXMLBWSTREAK | SEASON_FUTURE | season_stat_future |
| KXNEXTMANAGERMLB | EVENT | league_news_event |
| KXNEXTTEAMMLB | EVENT | league_news_event |
| KXTEAMSINWS | SEASON_FUTURE | pennant_world_series_future |
| KXWSAL | SEASON_FUTURE | pennant_world_series_future |
| KXWSNL | SEASON_FUTURE | pennant_world_series_future |
| MLBCBA | CORPORATE | league_business_event |

## Unclassified (needs review)

_none_
