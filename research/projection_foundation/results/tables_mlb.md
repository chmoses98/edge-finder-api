# MLB Projection Foundation — generated tables

Generated 2026-10-17T06:40:17Z, code cd88bfb166.


### batters_faced (pitchers; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 4852 | 2.872 | 3.954 | +0.035 | 2.0694 | 0.812 | 0.978 | 0.1592 |  | -0.1313 [-0.1474, -0.1150] | baseline |
| B | empirical_resid | 4852 | 2.834 | 3.903 | -0.009 | 2.0438 | 0.812 | 0.963 | 0.1577 | -0.0256 [-0.0424, -0.0097] |  | rejected |
| C | empirical_resid | 4852 | 2.814 | 3.890 | +0.004 | 2.0342 | 0.814 | 0.968 | 0.1569 | -0.0352 [-0.0520, -0.0195] |  | rejected |
| D | empirical_resid | 4852 | 2.813 | 3.890 | -0.000 | 2.0345 | 0.814 | 0.965 | 0.1568 | -0.0349 [-0.0515, -0.0187] | -0.1632 [-0.1764, -0.1500] | rejected |
| D_rc | empirical_resid | 4852 | 2.605 | 3.537 | +0.112 | 1.8851 | 0.814 | 0.975 | 0.1498 | -0.1843 [-0.2188, -0.1481] | -0.2019 [-0.2195, -0.1839] | rejected |
| E | empirical_resid | 4852 | 2.527 | 3.424 | +0.072 | 1.8268 | 0.814 | 0.958 | 0.1457 | -0.2426 [-0.2787, -0.2048] | -0.2486 [-0.2692, -0.2288] | best |
| F | empirical_resid | 4852 | 2.527 | 3.432 | +0.080 | 1.8286 | 0.813 | 0.965 | 0.1458 | -0.2408 [-0.2760, -0.2042] |  | competitive |

Holdout (2026, frozen **E**): n=4854, MAE 2.617, RMSE 3.579, bias +0.046, CRPS 1.8919, cover80 0.808, PIT var 0.976; CRPS vs A -0.3785 [-0.4275, -0.3317]; vs incumbent -0.2488 [-0.2676, -0.2288] (rung RPS over incumbent rungs 15..30 (matched n=4524); MAE diff -0.2287 [-0.2628, -0.1944])


### outs (pitchers; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 4852 | 2.907 | 3.857 | +0.013 | 2.0863 | 0.811 | 0.956 | 0.1858 |  | +0.0179 [+0.0053, +0.0295] | baseline |
| B | empirical_resid | 4852 | 2.885 | 3.823 | -0.030 | 2.0676 | 0.819 | 0.952 | 0.1843 | -0.0188 [-0.0317, -0.0055] |  | rejected |
| C | empirical_resid | 4852 | 2.732 | 3.619 | +0.058 | 1.9555 | 0.816 | 0.962 | 0.1786 | -0.1308 [-0.1538, -0.1055] |  | rejected |
| D | empirical_resid | 4852 | 2.732 | 3.619 | +0.056 | 1.9557 | 0.815 | 0.965 | 0.1785 | -0.1306 [-0.1540, -0.1043] | -0.0403 [-0.0537, -0.0286] | rejected |
| E | empirical_resid | 4852 | 2.698 | 3.574 | +0.023 | 1.9297 | 0.814 | 0.946 | 0.1758 | -0.1567 [-0.1832, -0.1296] |  | competitive |
| F | empirical_resid | 4852 | 2.696 | 3.572 | +0.035 | 1.9281 | 0.813 | 0.956 | 0.1757 | -0.1582 [-0.1825, -0.1322] | -0.0639 [-0.0789, -0.0506] | best |

Holdout (2026, frozen **F**): n=4854, MAE 2.709, RMSE 3.580, bias +0.064, CRPS 1.9360, cover80 0.819, PIT var 0.961; CRPS vs A -0.2552 [-0.2862, -0.2241]; vs incumbent -0.0763 [-0.0914, -0.0631] (rung RPS over incumbent rungs 9..21 (matched n=4524); MAE diff -0.1246 [-0.1501, -0.1018])


### pitcher_strikeouts (pitchers; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 4852 | 1.830 | 2.298 | -0.046 | 1.2818 | 0.803 | 0.987 | 0.1608 |  | +0.0088 [+0.0009, +0.0176] | baseline |
| B | empirical_resid | 4852 | 1.827 | 2.293 | -0.005 | 1.2786 | 0.804 | 0.985 | 0.1604 | -0.0033 [-0.0087, +0.0025] |  | rejected |
| C | nb_binned | 4852 | 1.797 | 2.264 | +0.069 | 1.2570 | 0.802 | 0.999 | 0.1577 | -0.0248 [-0.0331, -0.0163] |  | rejected |
| D | binom_mix | 4852 | 1.765 | 2.229 | +0.026 | 1.2386 | 0.784 | 1.027 | 0.1553 | -0.0432 [-0.0536, -0.0320] | -0.0217 [-0.0282, -0.0159] | competitive |
| E | nb_binned | 4852 | 1.762 | 2.222 | +0.069 | 1.2339 | 0.799 | 0.994 | 0.1548 | -0.0479 [-0.0590, -0.0360] |  | competitive |
| F | binom_mix | 4852 | 1.758 | 2.219 | +0.046 | 1.2320 | 0.787 | 1.019 | 0.1545 | -0.0498 [-0.0607, -0.0383] | -0.0283 [-0.0353, -0.0217] | best |

Holdout (2026, frozen **F**): n=4854, MAE 1.734, RMSE 2.176, bias +0.061, CRPS 1.2049, cover80 0.802, PIT var 0.996; CRPS vs A -0.0658 [-0.0767, -0.0552]; vs incumbent -0.0323 [-0.0390, -0.0255] (rung RPS over incumbent rungs 2..10 (matched n=4524); MAE diff -0.0513 [-0.0628, -0.0397])


### pitcher_walks (pitchers; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | binom_mix | 4852 | 1.023 | 1.265 | +0.017 | 0.6911 | 0.800 | 1.002 | 0.1651 |  |  | baseline |
| B | binom_mix | 4852 | 1.025 | 1.267 | +0.018 | 0.6923 | 0.796 | 1.004 | 0.1655 | +0.0013 [-0.0005, +0.0034] |  | rejected |
| C | empirical_resid | 4852 | 1.016 | 1.258 | +0.022 | 0.6863 | 0.795 | 1.010 | 0.1640 | -0.0047 [-0.0076, -0.0019] |  | competitive |
| D | empirical_resid | 4852 | 1.015 | 1.255 | +0.023 | 0.6844 | 0.794 | 1.018 | 0.1635 | -0.0066 [-0.0099, -0.0030] |  | best |
| E | binom_mix | 4852 | 1.010 | 1.251 | -0.010 | 0.6825 | 0.802 | 0.998 | 0.1630 | -0.0085 [-0.0127, -0.0037] |  | competitive |
| F | empirical_resid | 4852 | 1.014 | 1.252 | +0.027 | 0.6831 | 0.798 | 1.015 | 0.1632 | -0.0080 [-0.0115, -0.0041] |  | competitive |

Holdout (2026, frozen **D**): n=4854, MAE 1.000, RMSE 1.259, bias -0.020, CRPS 0.6806, cover80 0.806, PIT var 0.989; CRPS vs A -0.0114 [-0.0153, -0.0074]


### pitcher_hits_allowed (pitchers; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | binom_mix | 4852 | 1.750 | 2.178 | +0.035 | 1.2185 | 0.804 | 0.988 | 0.1910 |  |  | baseline |
| B | empirical_resid | 4852 | 1.747 | 2.174 | +0.015 | 1.2198 | 0.816 | 0.963 | 0.1910 | +0.0013 [-0.0018, +0.0049] |  | rejected |
| C | empirical_resid | 4852 | 1.709 | 2.123 | +0.046 | 1.1872 | 0.812 | 0.981 | 0.1859 | -0.0313 [-0.0400, -0.0230] |  | rejected |
| D | empirical_resid | 4852 | 1.702 | 2.110 | +0.053 | 1.1807 | 0.809 | 0.984 | 0.1849 | -0.0378 [-0.0466, -0.0294] |  | rejected |
| E | empirical_resid | 4852 | 1.684 | 2.096 | +0.051 | 1.1702 | 0.809 | 0.976 | 0.1829 | -0.0482 [-0.0585, -0.0375] |  | competitive |
| F | binom_mix | 4852 | 1.681 | 2.090 | +0.045 | 1.1677 | 0.801 | 1.000 | 0.1826 | -0.0507 [-0.0601, -0.0409] |  | best |

Holdout (2026, frozen **F**): n=4854, MAE 1.669, RMSE 2.086, bias +0.040, CRPS 1.1613, cover80 0.796, PIT var 1.003; CRPS vs A -0.0749 [-0.0863, -0.0634]


### pitcher_runs_allowed (pitchers; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 4852 | 1.653 | 2.033 | -0.003 | 1.1205 | 0.803 | 0.977 | 0.1911 |  |  | baseline |
| B | empirical_resid | 4852 | 1.659 | 2.038 | +0.020 | 1.1233 | 0.803 | 0.979 | 0.1915 | +0.0028 [-0.0000, +0.0055] |  | rejected |
| C | empirical_resid | 4852 | 1.640 | 2.018 | +0.039 | 1.1101 | 0.797 | 1.004 | 0.1890 | -0.0104 [-0.0154, -0.0059] |  | competitive |
| D | empirical_resid | 4852 | 1.632 | 2.010 | +0.041 | 1.1055 | 0.799 | 1.003 | 0.1882 | -0.0151 [-0.0198, -0.0104] |  | competitive |
| E | empirical_resid | 4852 | 1.629 | 2.006 | +0.069 | 1.1023 | 0.803 | 0.989 | 0.1876 | -0.0182 [-0.0243, -0.0127] |  | competitive |
| F | empirical_resid | 4852 | 1.621 | 2.003 | +0.048 | 1.1009 | 0.795 | 0.998 | 0.1874 | -0.0196 [-0.0251, -0.0143] |  | best |

Holdout (2026, frozen **F**): n=4854, MAE 1.614, RMSE 2.000, bias +0.059, CRPS 1.0923, cover80 0.806, PIT var 0.986; CRPS vs A -0.0319 [-0.0386, -0.0256]


### plate_appearances (hitters; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 43668 | 0.560 | 0.749 | +0.001 | 0.3738 | 0.798 | 1.009 | 0.1580 |  |  | baseline |
| B | empirical_resid | 43668 | 0.564 | 0.745 | -0.007 | 0.3713 | 0.797 | 1.010 | 0.1567 | -0.0025 [-0.0033, -0.0016] |  | rejected |
| C | empirical_resid | 43668 | 0.554 | 0.720 | -0.007 | 0.3556 | 0.794 | 1.017 | 0.1490 | -0.0182 [-0.0202, -0.0163] |  | rejected |
| D | empirical_resid | 43668 | 0.552 | 0.717 | -0.013 | 0.3537 | 0.795 | 1.014 | 0.1480 | -0.0201 [-0.0221, -0.0181] |  | rejected |
| E | empirical_resid | 43668 | 0.546 | 0.707 | -0.008 | 0.3482 | 0.800 | 1.004 | 0.1457 | -0.0256 [-0.0273, -0.0238] |  | competitive |
| F | empirical_resid | 43668 | 0.547 | 0.706 | -0.010 | 0.3478 | 0.796 | 1.012 | 0.1455 | -0.0260 [-0.0276, -0.0244] |  | best |
| C_hist_slot | empirical_resid | 43668 | 0.560 | 0.736 | -0.007 | 0.3656 | 0.795 | 1.014 | 0.1538 | -0.0082 [-0.0102, -0.0064] |  | rejected |
| D_hist_slot | empirical_resid | 43668 | 0.557 | 0.733 | -0.012 | 0.3636 | 0.796 | 1.012 | 0.1529 | -0.0102 [-0.0122, -0.0083] |  | rejected |
| E_hist_slot | empirical_resid | 43668 | 0.547 | 0.718 | -0.007 | 0.3554 | 0.799 | 1.008 | 0.1492 | -0.0185 [-0.0201, -0.0168] |  | rejected |

Holdout (2026, frozen **F**): n=43686, MAE 0.572, RMSE 0.735, bias -0.002, CRPS 0.3662, cover80 0.780, PIT var 1.040; CRPS vs A -0.0234 [-0.0254, -0.0216]; vs incumbent -0.0888 [-0.1038, -0.0756] (CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE diff -0.0885 [-0.1082, -0.0703])


### hits (hitters; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 43668 | 0.684 | 0.875 | +0.006 | 0.4577 | 0.798 | 1.008 | 0.1503 |  |  | baseline |
| B | empirical_resid | 43668 | 0.684 | 0.874 | +0.000 | 0.4574 | 0.797 | 1.010 | 0.1502 | -0.0003 [-0.0006, -0.0000] |  | competitive |
| C | binom_mix | 43668 | 0.684 | 0.873 | +0.004 | 0.4565 | 0.796 | 1.013 | 0.1499 | -0.0012 [-0.0017, -0.0008] |  | competitive |
| D | binom_mix | 43668 | 0.685 | 0.871 | +0.001 | 0.4552 | 0.796 | 1.012 | 0.1495 | -0.0025 [-0.0032, -0.0018] |  | competitive |
| E | binom_mix | 43668 | 0.684 | 0.870 | +0.004 | 0.4547 | 0.796 | 1.011 | 0.1493 | -0.0030 [-0.0037, -0.0022] |  | competitive |
| F | binom_mix | 43668 | 0.684 | 0.870 | +0.000 | 0.4547 | 0.797 | 1.011 | 0.1493 | -0.0030 [-0.0037, -0.0023] |  | best |
| D_hist_slot | binom_mix | 43668 | 0.685 | 0.872 | +0.001 | 0.4558 | 0.796 | 1.013 | 0.1497 | -0.0019 [-0.0026, -0.0011] |  | competitive |

Holdout (2026, frozen **F**): n=43686, MAE 0.683, RMSE 0.866, bias +0.006, CRPS 0.4518, cover80 0.801, PIT var 1.001; CRPS vs A -0.0028 [-0.0035, -0.0022]; vs incumbent -0.0110 [-0.0140, -0.0081] (CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE diff -0.0122 [-0.0166, -0.0082])


### total_bases (hitters; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | tb_mix | 43668 | 1.343 | 1.767 | -0.038 | 0.8831 | 0.795 | 1.023 | 0.1872 |  |  | baseline |
| B | tb_mix | 43668 | 1.337 | 1.766 | -0.047 | 0.8825 | 0.793 | 1.025 | 0.1870 | -0.0006 [-0.0012, +0.0001] |  | rejected |
| C | tb_mix | 43668 | 1.351 | 1.763 | +0.007 | 0.8809 | 0.797 | 1.019 | 0.1867 | -0.0022 [-0.0030, -0.0013] |  | competitive |
| D | tb_mix | 43668 | 1.347 | 1.761 | +0.005 | 0.8797 | 0.797 | 1.018 | 0.1864 | -0.0034 [-0.0045, -0.0023] |  | competitive |
| E | empirical_resid | 43668 | 1.346 | 1.761 | +0.003 | 0.8804 | 0.801 | 1.005 | 0.1865 | -0.0027 [-0.0039, -0.0015] |  | competitive |
| F | tb_mix | 43668 | 1.345 | 1.759 | +0.005 | 0.8785 | 0.797 | 1.017 | 0.1861 | -0.0046 [-0.0057, -0.0036] |  | best |
| D_hist_slot | tb_mix | 43668 | 1.349 | 1.762 | +0.004 | 0.8804 | 0.797 | 1.018 | 0.1865 | -0.0027 [-0.0037, -0.0016] |  | competitive |

Holdout (2026, frozen **F**): n=43686, MAE 1.333, RMSE 1.730, bias +0.023, CRPS 0.8652, cover80 0.804, PIT var 1.006; CRPS vs A -0.0050 [-0.0062, -0.0039]; vs incumbent -0.0142 [-0.0175, -0.0109] (CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE diff -0.0075 [-0.0124, -0.0021])


### home_runs (hitters; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | binom_mix | 43668 | 0.217 | 0.356 | -0.003 | 0.1107 | 0.798 | 1.000 | 0.1021 |  |  | baseline |
| B | binom_mix | 43668 | 0.216 | 0.355 | -0.004 | 0.1104 | 0.798 | 1.000 | 0.1018 | -0.0003 [-0.0004, -0.0002] |  | competitive |
| C | binom_mix | 43668 | 0.219 | 0.355 | +0.000 | 0.1103 | 0.800 | 0.996 | 0.1017 | -0.0004 [-0.0005, -0.0003] |  | competitive |
| D | binom_mix | 43668 | 0.220 | 0.355 | +0.002 | 0.1102 | 0.801 | 0.994 | 0.1016 | -0.0005 [-0.0006, -0.0003] |  | competitive |
| E | empirical_resid | 43668 | 0.217 | 0.355 | -0.002 | 0.1104 | 0.797 | 1.000 | 0.1019 | -0.0003 [-0.0004, -0.0001] |  | competitive |
| F | binom_mix | 43668 | 0.219 | 0.355 | +0.001 | 0.1100 | 0.800 | 0.995 | 0.1015 | -0.0006 [-0.0008, -0.0005] |  | best |
| D_hist_slot | binom_mix | 43668 | 0.220 | 0.355 | +0.002 | 0.1102 | 0.801 | 0.994 | 0.1017 | -0.0005 [-0.0006, -0.0003] |  | competitive |

Holdout (2026, frozen **F**): n=43686, MAE 0.217, RMSE 0.348, bias +0.005, CRPS 0.1074, cover80 0.803, PIT var 0.988; CRPS vs A -0.0006 [-0.0008, -0.0004]; vs incumbent -0.0026 [-0.0034, -0.0019] (CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE diff 0.0279 [0.0264, 0.0296])


### runs (hitters; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | poisson | 43668 | 0.580 | 0.680 | -0.009 | 0.3203 | 0.798 | 1.006 | 0.1539 |  |  | baseline |
| B | poisson | 43668 | 0.577 | 0.680 | -0.012 | 0.3199 | 0.798 | 1.008 | 0.1536 | -0.0005 [-0.0007, -0.0003] |  | competitive |
| C | poisson | 43668 | 0.580 | 0.679 | +0.002 | 0.3192 | 0.800 | 0.999 | 0.1533 | -0.0011 [-0.0014, -0.0008] |  | competitive |
| D | binom_mix | 43668 | 0.576 | 0.677 | -0.005 | 0.3182 | 0.794 | 1.012 | 0.1528 | -0.0022 [-0.0027, -0.0016] |  | competitive |
| E | empirical_resid | 43668 | 0.578 | 0.678 | -0.006 | 0.3189 | 0.797 | 1.008 | 0.1532 | -0.0014 [-0.0019, -0.0009] |  | competitive |
| F | binom_mix | 43668 | 0.576 | 0.676 | -0.002 | 0.3178 | 0.795 | 1.010 | 0.1526 | -0.0025 [-0.0031, -0.0019] |  | best |
| D_hist_slot | binom_mix | 43668 | 0.577 | 0.678 | -0.005 | 0.3186 | 0.794 | 1.013 | 0.1530 | -0.0017 [-0.0023, -0.0011] |  | competitive |

Holdout (2026, frozen **F**): n=43686, MAE 0.575, RMSE 0.672, bias +0.002, CRPS 0.3164, cover80 0.801, PIT var 0.995; CRPS vs A -0.0024 [-0.0029, -0.0018]; vs incumbent -0.0091 [-0.0116, -0.0065] (CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE diff 0.0152 [0.0121, 0.0187])


### rbis (hitters; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 43668 | 0.633 | 0.844 | -0.004 | 0.3524 | 0.799 | 1.004 | 0.1118 |  |  | baseline |
| B | empirical_resid | 43668 | 0.633 | 0.844 | -0.002 | 0.3521 | 0.800 | 1.004 | 0.1118 | -0.0002 [-0.0004, -0.0000] |  | competitive |
| C | empirical_resid | 43668 | 0.631 | 0.844 | -0.003 | 0.3519 | 0.800 | 1.004 | 0.1117 | -0.0004 [-0.0007, -0.0001] |  | competitive |
| D | empirical_resid | 43668 | 0.629 | 0.843 | -0.005 | 0.3514 | 0.799 | 1.006 | 0.1115 | -0.0009 [-0.0014, -0.0005] |  | competitive |
| E | empirical_resid | 43668 | 0.628 | 0.843 | -0.011 | 0.3512 | 0.799 | 1.007 | 0.1114 | -0.0012 [-0.0016, -0.0008] |  | competitive |
| F | empirical_resid | 43668 | 0.627 | 0.842 | -0.008 | 0.3510 | 0.799 | 1.007 | 0.1114 | -0.0014 [-0.0018, -0.0010] |  | best |
| D_hist_slot | empirical_resid | 43668 | 0.630 | 0.843 | -0.004 | 0.3515 | 0.799 | 1.005 | 0.1115 | -0.0009 [-0.0013, -0.0005] |  | competitive |

Holdout (2026, frozen **F**): n=43686, MAE 0.626, RMSE 0.832, bias -0.002, CRPS 0.3476, cover80 0.800, PIT var 0.996; CRPS vs A -0.0013 [-0.0018, -0.0009]; vs incumbent -0.0139 [-0.0166, -0.0111] (CRPS vs production hitter engine rerun, matched n=8883 (2026-08-16..2026-09-27); MAE diff 0.0473 [0.0436, 0.0513])


### hits_runs_rbis (hitters; validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 43668 | 1.513 | 1.932 | +0.002 | 1.0042 | 0.798 | 1.008 | 0.1798 |  |  | baseline |
| B | empirical_resid | 43668 | 1.510 | 1.931 | -0.008 | 1.0035 | 0.797 | 1.010 | 0.1797 | -0.0007 [-0.0013, -0.0000] |  | competitive |
| C | empirical_resid | 43668 | 1.507 | 1.928 | -0.004 | 1.0019 | 0.797 | 1.010 | 0.1794 | -0.0022 [-0.0032, -0.0012] |  | competitive |
| D | empirical_resid | 43668 | 1.501 | 1.923 | -0.009 | 0.9993 | 0.797 | 1.013 | 0.1789 | -0.0049 [-0.0066, -0.0032] |  | competitive |
| E | empirical_resid | 43668 | 1.500 | 1.923 | -0.015 | 0.9990 | 0.798 | 1.009 | 0.1788 | -0.0052 [-0.0067, -0.0037] |  | competitive |
| F | empirical_resid | 43668 | 1.500 | 1.921 | -0.010 | 0.9976 | 0.798 | 1.011 | 0.1785 | -0.0066 [-0.0083, -0.0049] |  | best |
| D_hist_slot | empirical_resid | 43668 | 1.505 | 1.925 | -0.008 | 1.0005 | 0.798 | 1.011 | 0.1791 | -0.0036 [-0.0053, -0.0019] |  | competitive |

Holdout (2026, frozen **F**): n=43686, MAE 1.493, RMSE 1.906, bias +0.001, CRPS 0.9911, cover80 0.803, PIT var 1.001; CRPS vs A -0.0059 [-0.0075, -0.0043]


### team_runs (validation 2025)

| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| A | empirical_resid | 4852 | 2.524 | 3.243 | -0.025 | 1.7567 | 0.784 | 1.032 | 0.2072 |  |  | baseline |
| B | empirical_resid | 4852 | 2.521 | 3.237 | -0.008 | 1.7537 | 0.790 | 1.026 | 0.2068 | -0.0030 [-0.0052, -0.0004] |  | competitive |
| C | empirical_resid | 4852 | 2.502 | 3.217 | -0.038 | 1.7432 | 0.787 | 1.028 | 0.2056 | -0.0134 [-0.0198, -0.0071] |  | competitive |
| D | nb_binned | 4852 | 2.499 | 3.205 | +0.015 | 1.7372 | 0.776 | 1.050 | 0.2049 | -0.0195 [-0.0283, -0.0108] |  | best |
| D_ridge | empirical_resid | 4852 | 2.501 | 3.212 | +0.014 | 1.7409 | 0.786 | 1.029 | 0.2053 | -0.0158 [-0.0240, -0.0073] |  | competitive |
| E | empirical_resid | 4852 | 2.513 | 3.216 | +0.072 | 1.7431 | 0.790 | 1.025 | 0.2055 | -0.0136 [-0.0222, -0.0056] |  | competitive |
| F | empirical_resid | 4852 | 2.501 | 3.207 | +0.031 | 1.7385 | 0.789 | 1.029 | 0.2049 | -0.0182 [-0.0272, -0.0098] |  | competitive |

Holdout (2026, frozen **D**): n=4854, MAE 2.498, RMSE 3.175, bias +0.027, CRPS 1.7248, cover80 0.789, PIT var 1.042; CRPS vs A -0.0197 [-0.0272, -0.0123]; vs incumbent -0.0169 [-0.0497, +0.0140] (CRPS vs archived production projection (NB alpha 0.2815), matched n=614; MAE diff 0.0233 [-0.0309, 0.0761])

