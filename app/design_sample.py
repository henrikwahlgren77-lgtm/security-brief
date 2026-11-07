"""Fictional sample content for the design preview (/design).

EVERYTHING HERE IS INVENTED. Sources are named "(exempel)" and point at the reserved
.example domain; nothing describes a real event, agency finding or assessment. The preview
routes are off unless DESIGN_PREVIEW=1 and every preview page carries a banner saying so.
"""

from __future__ import annotations

from typing import Any

SOURCE = "https://kalla.example/"


def _src(n: int) -> str:
    return f"{SOURCE}{n}"


LATEST_MARKDOWN = f"""## Sammanfattning

- Ett handelsfartyg utan aktiv AIS-signal observerades under flera timmar nära en kabelkorridor i södra Östersjön. Inga skador har rapporterats.
- Finländska och estniska myndigheter bekräftar ökad militär flygaktivitet vid Finska viken. Aktiviteten ligger inom tidigare observerade mönster, men frekvensen har ökat.
- En kommunal IT-leverantör rapporterar ett it-angrepp. Uppgifter om gärningsman saknas.
- Inför ministermötet nästa vecka skärps tonen i flera officiella uttalanden. Ingen konkret förändring av militär hållning har rapporterats.

## Verifierade fakta

- Fartyget stannade över en kabelkorridor under cirka fem timmar natten mot söndag, enligt [Kustbevakningens lägesbild (exempel), 8 nov]({_src(1)}).
- Estland och Finland rapporterar var för sig ökad flygaktivitet nära luftrummet, enligt [Försvarsmaktens veckorapport (exempel), 7 nov]({_src(2)}) och [Gränsbevakningens pressmeddelande (exempel), 7 nov]({_src(3)}).
- IT-leverantören bekräftar att vissa tjänster var otillgängliga under gårdagen, enligt [bolagets driftinformation (exempel), 7 nov]({_src(4)}).

## Myndigheters och regeringars bedömningar

- Säkerhetsmyndigheten (exempel) bedömer att händelsen vid kabelkorridoren "inte i nuläget tyder på sabotage", men utreder fortfarande orsaken ([lägesbild, 8 nov]({_src(1)})).
- Försvarsmakten (exempel) bedömer att den ökade flygaktiviteten är förenlig med säsongsvariationer och övningsverksamhet ([veckorapport, 7 nov]({_src(2)})).
- Regeringen (exempel) uppger att man följer utvecklingen "nära tillsammans med allierade" ([uttalande, 8 nov]({_src(5)})).

## Mediarapportering

- Flera medier uppger att fartyget tidigare har anlöpt hamnar utanför regionen. Uppgiften bygger på en enda anonym källa och har inte kunnat bekräftas ([tidningen Exempelposten, 8 nov]({_src(6)})).

## Säkerhetsläget i Norden och Östersjöregionen

Läget bedöms som i huvudsak oförändrat i de nordiska länderna, med ökad uppmärksamhet kring infrastruktur i södra Östersjön. De enskilda händelserna är var för sig förenliga med flera förklaringar, men sammantaget motiverar de fortsatt bevakning.

Direkt militärt hot mot Sverige: inga nya tecken i dagens underlag. Hybridaktivitet: en händelse med oklar orsak vid kritisk infrastruktur. Artikel 5 är ett politiskt och juridiskt beslut inom NATO. Dagens material innehåller inga indikatorer som motiverar en sådan bedömning.

## Redaktionell analys

En enskild händelse behöver inte innebära en förändrad hotbild. Men tre samtidiga signaler, en oklar händelse vid infrastruktur, ökad flygaktivitet och skärpt retorik inför ett politiskt möte, kan peka åt samma håll eller vara en slump. Den starkaste tolkningen just nu är att underlaget är otillräckligt för att skilja mellan dem. Följ fartygets fortsatta rörelser, utredningens slutsatser och om flygaktiviteten består efter mötet.

## Osäkerheter och luckor

- Orsaken till fartygets stopp är inte klarlagd. Utredningen pågår (O1, väsentlig invändning från den oberoende granskningen: stoppet kan ha en rutinmässig förklaring som inte har undersökts).
- Uppgiften om fartygets tidigare anlöp bygger på en enda anonym källa.
- Det finns ingen oberoende uppgift om it-angreppets upphovsman.

## Källor

- [Kustbevakningens lägesbild (exempel), 8 nov]({_src(1)})
- [Försvarsmaktens veckorapport (exempel), 7 nov]({_src(2)})
- [Gränsbevakningens pressmeddelande (exempel), 7 nov]({_src(3)})
- [IT-leverantörens driftinformation (exempel), 7 nov]({_src(4)})
- [Regeringens uttalande (exempel), 8 nov]({_src(5)})
- [Exempelposten (exempel), 8 nov]({_src(6)})
"""


def _report(date: str, title: str, points: list[str], fact: str, analysis: str) -> str:
    bullets = "\n".join(f"- {p}" for p in points)
    return f"""## Sammanfattning

{bullets}

## Verifierade fakta

- {fact} ([källa (exempel)]({_src(11)}))

## Myndigheters och regeringars bedömningar

- Ansvarig myndighet (exempel) bedömer läget som oförändrat ([källa (exempel)]({_src(12)})).

## Mediarapportering

- Inga ytterligare uppgifter från medier som kunnat bekräftas dagen {date}.

## Säkerhetsläget i Norden och Östersjöregionen

Läget bedöms som i huvudsak oförändrat. Inga nya tecken på förändrad militär hållning i dagens underlag.

## Redaktionell analys

{analysis}

## Osäkerheter och luckor

- Underlaget för dagen är begränsat.

## Källor

- [Källa 1 (exempel)]({_src(11)})
- [Källa 2 (exempel)]({_src(12)})
"""


_OLDER = [
    ("2026-11-07", "Säkerhetsbrief – 7 november 2026", ("Hybridhot", "Infrastruktur"),
     ["Ökad it-trafik mot kommunala webbplatser noterades under gårdagens morgon, utan bekräftad påverkan.", "Tidigare uppgifter om flygaktivitet vid Finska viken är oförändrade."],
     "Trafikökningen avtog före lunch enligt flera driftsinformationer.", "Händelsen är ett exempel på brus som kan likna en förberedelse. Underlaget räcker inte för att avgöra vilket."),
    ("2026-11-06", "Säkerhetsbrief – 6 november 2026", ("Militärt", "NATO"),
     ["Ett allierat flottbesök meddelades till Östersjön under nästa vecka.", "Inga nya uppgifter om ryska marinrörelser."],
     "Flottbesöket bekräftas i ett officiellt pressmeddelande.", "Planerade besök är ordinarie verksamhet. Det intressanta är hur de tolkas i offentliga uttalanden."),
    ("2026-11-05", "Säkerhetsbrief – 5 november 2026", ("Politik", "Hybridhot"),
     ["Ett ministermöte om gemensam beredskap annonserades.", "En desinformationskampanj om beredskapslager identifierades av en granskare."],
     "Mötet bekräftas av värdlandets regering.", "Kampanjens räckvidd är oklar. Den bör bedömas separat från mötets innehåll."),
    ("2026-11-04", "Säkerhetsbrief – 4 november 2026", ("Cyber",),
     ["En svit sårbarheter i ett vanligt fjärråtkomstverktyg publicerades med rekommenderade åtgärder."],
     "Tillverkaren har publicerat en säkerhetsuppdatering.", "Åtgärdstakten hos kommunala organisationer är den relevanta indikatorn."),
    ("2026-10-31", "Säkerhetsbrief – 31 oktober 2026", ("Militärt",),
     ["Veckosammanfattning: övningsverksamhet i flera länder, inga avvikelser från planerade mönster."],
     "Övningarna var förhandsanmälda.", "En lugn vecka är också ett resultat. Det bör inte tolkas som att risken har försvunnit."),
    ("2026-10-30", "Säkerhetsbrief – 30 oktober 2026", ("Terrorhot",),
     ["Hotnivån bedömdes som oförändrad av den ansvariga myndigheten."],
     "Hotnivån ligger kvar enligt myndighetens publicerade bedömning.", "Oförändrat läge är en bedömning, inte en frånvaro av bedömning."),
    ("2026-10-29", "Säkerhetsbrief – 29 oktober 2026", ("Hybridhot", "Infrastruktur"),
     ["Störningar i satellitnavigering rapporterades av flera fartyg i norra Östersjön.", "Orsaken är inte klarlagd."],
     "Flera fartyg rapporterade störningar samma natt.", "Störningar i satellitnavigering har flera möjliga förklaringar, vilka behöver hållas öppna."),
]

LATEST = {
    "report_date": "2026-11-08",
    "title": "Säkerhetsbrief – 8 november 2026",
    "markdown": LATEST_MARKDOWN,
    "updated_at": "2026-11-08T06:42:00+00:00",
    "published_at": "2026-11-08T06:42:00+00:00",
    "topics": ("Hybridhot", "Infrastruktur", "Militärt", "Cyber"),
    "status": "published",
}

ROWS: list[dict[str, Any]] = [LATEST] + [
    {"report_date": d, "title": t, "markdown": _report(d, t, pts, fact, analysis), "updated_at": f"{d}T06:30:00+00:00",
     "published_at": f"{d}T06:30:00+00:00", "topics": topics, "status": "published"}
    for d, t, topics, pts, fact, analysis in _OLDER
]

HEADLINE = "Säkerhetsläget i norra Europa förändras"
STANDFIRST = ("Militär aktivitet, ökade säkerhetspolitiska spänningar och förändringar i den regionala hotbilden "
              "påverkar utvecklingen i Norden och Östersjöregionen.")

DEVELOPMENTS = [
    {"kind": "new", "label": "Nytt", "text": "Handelsfartyg utan aktiv AIS-signal låg stilla över en kabelkorridor i södra Östersjön i cirka fem timmar. Orsaken utreds. Inga skador har rapporterats."},
    {"kind": "change", "label": "Förändring", "text": "Finland och Estland rapporterar ökad flygaktivitet vid Finska viken. Aktiviteten är inom tidigare mönster, men frekvensen har ökat jämfört med föregående vecka."},
    {"kind": "facts", "label": "Bekräftat", "text": "En kommunal it-leverantör bekräftar störningar under gårdagen. Uppgift om gärningsman saknas."},
    {"kind": "background", "label": "Bakgrund", "text": "Skärpt ton i officiella uttalanden inför ministermötet nästa vecka. Ingen konkret förändring av militär hållning har rapporterats."},
]

REGIONS = [
    {"name": "Sverige", "level": ("attention", "Ökad uppmärksamhet"), "change": ("change", "Förändring"), "comment": "Händelse vid kabelkorridor utanför kusten. Orsak oklar, ingen bekräftad påverkan."},
    {"name": "Finland och Estland", "level": ("attention", "Ökad uppmärksamhet"), "change": ("change", "Förändring"), "comment": "Ökad flygaktivitet nära luftrummet, inom tidigare mönster."},
    {"name": "Norge och Danmark", "level": ("unchanged", "Oförändrat"), "change": ("background", "Oförändrat"), "comment": "Inga nya uppgifter i dagens underlag."},
    {"name": "Lettland och Litauen", "level": ("unchanged", "Oförändrat"), "change": ("background", "Oförändrat"), "comment": "Rutinmässig övningsverksamhet enligt tidigare anmälan."},
    {"name": "Östersjön", "level": ("attention", "Ökad uppmärksamhet"), "change": ("new", "Nytt"), "comment": "Infrastruktur i södra Östersjön står i fokus efter dagens händelse."},
    {"name": "Ryssland och NATO", "level": ("unchanged", "Oförändrat"), "change": ("background", "Bakgrund"), "comment": "Retoriken skärps inför mötet. Inga konkreta militära förändringar rapporterade."},
]

CHANGES = [
    {"kind": "new", "label": "Nytt", "text": "Fartyget vid kabelkorridoren är en ny händelse sedan gårdagens rapport."},
    {"kind": "change", "label": "Förändring", "text": "Flygaktiviteten vid Finska viken bedömdes i går som oförändrad. Frekvensen har nu ökat."},
    {"kind": "confirmed", "label": "Bekräftelse", "text": "Gårdagens uppgift om trafikökning mot kommunala webbplatser bekräftas av driftsinformation."},
    {"kind": "background", "label": "Oförändrat", "text": "Hotnivån för terrorism är oförändrad enligt ansvarig myndighet."},
]

