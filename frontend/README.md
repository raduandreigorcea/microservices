# frontend

React + TypeScript + Vite. Talks only to the gateway.

## Rulare

```sh
cd frontend
npm install
npm run dev      # http://localhost:5173
```

Backend-ul trebuie să fie pornit (`docker compose up -d` din rădăcină). Dev
server-ul face proxy pe `/api` către gateway, așa că browserul vede o singură
origine și nu există CORS de configurat.

`VITE_GATEWAY_URL` (implicit `http://localhost:8000`) schimbă ținta proxy-ului.

## Autentificare

Butonul de login trimite browserul la `/auth/google/login` prin gateway. După
ce Google confirmă, `user_service` redirecționează la `LOGIN_SUCCESS_REDIRECT`
cu perechea de token-uri în fragmentul URL-ului. Aplicația o citește o
singură dată, o pune în `localStorage` și curăță bara de adrese.

`LOGIN_SUCCESS_REDIRECT` trebuie să fie `http://localhost:5173/`. E setat ca
implicit în `docker-compose.yml`.

Un 401 declanșează un singur refresh, partajat între apelurile concurente, și
o singură reîncercare. Dacă și acela pică, sesiunea e ștearsă.

## Structură

| cale | ce conține |
| --- | --- |
| `src/lib/api.ts` | singurul loc care vorbește cu gateway-ul |
| `src/lib/session.ts` | unde stau token-urile și cine află când se schimbă |
| `src/lib/auth.tsx` | cine e conectat, pentru restul aplicației |
| `src/lib/format.ts` | numere și date, în română |
| `src/styles/tokens.css` | tokenii de design, literali apoi semantici |
| `src/styles/base.css` | reset, tipografie, utilitare |
| `src/styles/app.css` | layout și componente |
| `src/routes/` | un fișier per ecran |

## Ecrane

- `/` panou: cât e indexat, ce rulează, ce a aterizat ultima dată
- `/companies` registrul căutabil, cu paginare în URL
- `/companies/:idno` o companie în întregime
- `/companies/:idno/statements/:year` un an, rând cu rând
- `/jobs` toate rulările, cu polling cât timp una e vie
- `/jobs/:id` o rulare urmărită live, cu buton de anulare
- `/scrape` pornește un job: listă de IDNO sau sweep

## Note de implementare

Stilurile folosesc cascade layers (`reset, base, layout, components,
utilities`), deci nu există convenții de tip BEM pentru specificitate.

Efectele care nu sunt esențiale sunt progressive enhancement: rândurile apar
cu scroll-driven animations (`animation-timeline: view()`), iar bara de sus
își desenează linia doar când chiar s-a lipit de vârf
(`@container scroll-state(stuck: top)`). Browserele fără ele arată pagina
normal. Tranzițiile între ecrane folosesc View Transitions prin
`<Link viewTransition>`.
