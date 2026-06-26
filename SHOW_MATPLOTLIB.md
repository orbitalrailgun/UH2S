# SHOW(table, matplotlib, {params}) — графики

Команда строит график matplotlib по табличным данным (результат `GET`). Связано:
[`HARVESTER_DSL.md`](HARVESTER_DSL.md).

```
SHOW(<table>, matplotlib, {<JSON-параметры>})
```
- `<table>` — имя таблицы (результат `GET ... AS <table>` или переменная-список словарей).
- третий аргумент — **JSON-объект** параметров (можно с запятыми/вложенностью — парсер это учитывает).

Данные = список словарей → строки DataFrame; ключи словарей = столбцы. По именам столбцов
вы и задаёте `x`/`y`. **Столбцы для `y` должны быть числовыми.**

---

## 1. Простой режим (один тип графика)

| Параметр | Тип | Описание |
|---|---|---|
| `kind` | str | `line` (деф.), `bar`, `barh`, `area`, `scatter`, `hist`, `box`, `pie` |
| `x` | str | столбец оси X (для line/bar/scatter) |
| `y` | str \| list | один столбец **или список** столбцов (несколько серий) |
| `color` | str \| list | цвет(а) серий (`"#06b6d4"` или `["#06b6d4","#f59e0b"]`) |
| `title`,`xlabel`,`ylabel` | str | подписи |
| `figsize` | [w,h] | размер в дюймах (деф. `[10,5]`) |
| `dpi` | int | резкость PNG (50–400, деф. 150) |
| `grid` | bool | сетка |
| `legend` | bool | легенда (деф. true) |
| `logx`,`logy` | bool | логарифмическая ось |
| `xlim`,`ylim` | [min,max] | пределы осей |
| `stacked`,`rot`,`alpha`,`width` | — | прокидываются в pandas.plot |

Примеры:
```
/* линия по одному столбцу */
GET sqlite3:query(queries=["SELECT day, value FROM data ORDER BY day"]) AS d
| SHOW(d, matplotlib, {"kind":"line","x":"day","y":"value","title":"Value over time","grid":true})

/* НЕСКОЛЬКО СЕРИЙ одним типом — y списком */
SHOW(d, matplotlib, {"kind":"line","x":"day","y":["cpu","mem","disk"],"ylabel":"%","grid":true})

/* столбчатый */
SHOW(d, matplotlib, {"kind":"bar","x":"host","y":"errors","color":"#06b6d4","title":"Errors by host"})
```

---

## 2. Пороговая/опорная линия (ваш случай: превышение bar)

`hlines` — горизонтальные линии (порог), `vlines` — вертикальные. Каждая: `{y|x, color, label, linestyle, linewidth}`.

```
/* столбцы + красная линия-порог 100, видно превышение */
GET sqlite3:query(queries=["SELECT host, errors FROM data ORDER BY errors DESC"]) AS agg
| SHOW(agg, matplotlib, {
    "kind":"bar", "x":"host", "y":"errors", "title":"Errors by host", "ylabel":"errors", "grid":true,
    "hlines":[{"y":100, "color":"red", "linestyle":"--", "label":"threshold 100"}]
  })
```
Можно несколько порогов и вертикальные отметки:
```
"hlines":[{"y":100,"color":"orange","label":"warn"},{"y":200,"color":"red","label":"crit"}],
"vlines":[{"x":5,"color":"gray","linestyle":":","label":"point"}]
```

---

## 3. Общий режим: несколько слоёв (`layers`)

Когда нужны **разные типы** на одном графике (bar + line), **разные столбцы**, **вторая ось Y** —
задайте `layers` (список слоёв). Каждый слой: `{kind, x, y, color, label, secondary_y, stacked, alpha, width}`.

```
/* столбцы (count) + линия среднего на ВТОРОЙ оси Y */
GET sqlite3:query(queries=["SELECT date, count, avg_latency FROM data ORDER BY date"]) AS d
| SHOW(d, matplotlib, {
    "title":"Requests vs latency", "grid":true, "xlabel":"date",
    "layers":[
      {"kind":"bar",  "x":"date", "y":"count",       "label":"requests",    "color":"#06b6d4"},
      {"kind":"line", "x":"date", "y":"avg_latency", "label":"avg latency", "color":"#f59e0b", "secondary_y":true}
    ]
  })
```
`hlines`/`vlines` можно задавать **на верхнем уровне** params (рекомендуется) **или внутри слоя** — оба
написания работают. Всё оформление (`title/xlabel/ylabel/grid/legend/logy/...`) работает и в режиме `layers`.
`secondary_y:true` рисует слой на правой оси Y (другой масштаб). Легенда объединяет обе оси и линии-пороги.

```
/* bar + порог + сглаживающая линия, всё вместе */
SHOW(d, matplotlib, {
  "title":"Errors with threshold", "grid":true, "ylabel":"errors",
  "layers":[
    {"kind":"bar",  "x":"host", "y":"errors",    "label":"errors", "color":"#3b82f6"},
    {"kind":"line", "x":"host", "y":"baseline",  "label":"baseline", "color":"#22c55e"}
  ],
  "hlines":[{"y":100, "color":"red", "linestyle":"--", "label":"limit"}]
})
```

---

## 3a. 3D-графики (`bar3d`, `scatter3d`)

Трёхмерный график по **трём осям**: `x`, `y` и `z` (третья ось — высота столбца / координата точки).
Это отдельная ветка рендера — `layers`, `hlines`/`vlines`, `secondary_y` и 2D-оформление здесь **не применяются**.

| Параметр | Описание |
|---|---|
| `kind` | `bar3d` (3D-столбцы) или `scatter3d` (3D-точки) |
| `x`,`y` | столбцы по горизонтали; числовые — как есть, строковые — категориями с подписями |
| `z` | числовой столбец: высота столбца (bar3d) / координата по Z (scatter3d) |
| `xlabel`,`ylabel`,`zlabel` | подписи осей (по умолчанию — имена столбцов) |
| `color` | цвет (`"#06b6d4"`) |
| `bar_width` | ширина/глубина столбца для `bar3d` (деф. 0.5) |
| `alpha` | прозрачность |
| `elev`,`azim` | угол обзора (вертикальный / горизонтальный поворот) |
| `title`,`figsize`,`dpi` | как в 2D |

```
/* 3D-столбцы: продажи по региону и месяцу */
GET sqlite3:query(queries=["SELECT region, month, sales FROM data"]) AS d
| SHOW(d, matplotlib, {
    "kind":"bar3d", "x":"region", "y":"month", "z":"sales",
    "title":"Sales by region/month", "zlabel":"sales",
    "color":"#06b6d4", "figsize":[10,7], "elev":25, "azim":-60
  })

/* 3D-точки */
SHOW(d, matplotlib, {"kind":"scatter3d", "x":"cpu", "y":"mem", "z":"latency",
    "title":"Latency vs cpu/mem", "figsize":[9,7]})
```

> При большом числе категорий 3D-столбцы перекрываются и плохо читаются — для плотных данных
> используйте 2D-агрегаты. Подберите `elev`/`azim`, если часть столбцов скрыта за другими.

---

## 4. Подготовка данных перед графиком

Часто проще сначала привести данные к нужной форме in-memory SQL, затем рисовать:
```
GET elastic_requests:query(...) AS raw
| GET sqlite3:query(queries=[
    "SELECT host, COUNT(*) AS errors FROM raw WHERE level='error' GROUP BY host ORDER BY errors DESC"
  ]) AS agg
| SHOW(agg, matplotlib, {"kind":"bar","x":"host","y":"errors",
    "hlines":[{"y":100,"color":"red","linestyle":"--","label":"limit"}]})
```

Подстановка переменных в параметры (через DEF + `%(name)X`):
```
DEF 100 AS limit
| GET sqlite3:query(queries=["SELECT host, errors FROM data"]) AS agg
| SHOW(agg, matplotlib, {"kind":"bar","x":"host","y":"errors","hlines":[{"y":%(limit)i,"color":"red"}]})
```

---

## 5. Если график не строится — что проверить

- **Пустой/непохожий график** — проверьте, что `x`/`y` точно равны **именам столбцов** в таблице
  (см. вкладку Data/Variables после выполнения — там видны столбцы).
- **`y` должен быть числовым.** Строковые столбцы по Y не строятся (или дают пусто). Приведите типы в SQL (`CAST(... AS REAL)`).
- **Несколько серий** — это `y:["c1","c2"]` (простой режим) или `layers:[...]` (разные типы/оси). Один `y` строкой рисует одну серию.
- **Параметры — валидный JSON.** Кавычки двойные, булевы `true/false`, без хвостовых запятых. Невалидный JSON → шаг сообщит «optional_params не является валидным JSON».
- **`secondary_y`** нужен, когда у серий сильно разный масштаб (например, count и проценты).
- **Размытость** — увеличьте `dpi` (до 400) и/или `figsize`.
