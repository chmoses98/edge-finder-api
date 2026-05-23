export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;
  const apiKey = process.env.WEATHER_API_KEY;

  // Ballpark coordinates and metadata
  const parks = [
    { team: 'Cincinnati Reds', city: 'Cincinnati', lat: 39.0979, lon: -84.5088, dome: false },
    { team: 'Toronto Blue Jays', city: 'Toronto', lat: 43.6414, lon: -79.3894, dome: true },
    { team: 'New York Yankees', city: 'New York', lat: 40.8296, lon: -73.9262, dome: false },
    { team: 'Chicago Cubs', city: 'Chicago', lat: 41.9484, lon: -87.6553, dome: false },
    { team: 'Baltimore Orioles', city: 'Baltimore', lat: 39.2838, lon: -76.6218, dome: false },
    { team: 'Philadelphia Phillies', city: 'Philadelphia', lat: 39.9061, lon: -75.1665, dome: false },
    { team: 'San Francisco Giants', city: 'San Francisco', lat: 37.7786, lon: -122.3893, dome: false },
    { team: 'Kansas City Royals', city: 'Kansas City', lat: 39.0517, lon: -94.4803, dome: false },
    { team: 'Boston Red Sox', city: 'Boston', lat: 42.3467, lon: -71.0972, dome: false },
    { team: 'Atlanta Braves', city: 'Atlanta', lat: 33.8908, lon: -84.4679, dome: false },
    { team: 'Miami Marlins', city: 'Miami', lat: 25.7781, lon: -80.2196, dome: true },
    { team: 'Milwaukee Brewers', city: 'Milwaukee', lat: 43.0280, lon: -87.9712, dome: true },
    { team: 'San Diego Padres', city: 'San Diego', lat: 32.7076, lon: -117.1570, dome: false },
    { team: 'Los Angeles Angels', city: 'Anaheim', lat: 33.8003, lon: -117.8827, dome: false },
    { team: 'Arizona Diamondbacks', city: 'Phoenix', lat: 33.4453, lon: -112.0667, dome: true },
  ];

  if (!apiKey) {
    // Return mock/static data without API key for testing
    const mockWeather = parks.map(p => ({
      team: p.team,
      city: p.city,
      dome: p.dome,
      note: p.dome ? 'Dome/retractable roof — weather not a factor' : 'Open air — check conditions',
      temp: null,
      wind: null,
      windDir: null,
      precip: null,
      condition: 'API key not configured — add WEATHER_API_KEY to Vercel env'
    }));
    const result = { parks: mockWeather, note: 'Add WEATHER_API_KEY env var for live data' };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);
  }

  try {
    // Fetch weather for all open-air parks in parallel
    const openParks = parks.filter(p => !p.dome);
    const weatherPromises = openParks.map(async p => {
      try {
        const url = `https://api.openweathermap.org/data/2.5/weather?lat=${p.lat}&lon=${p.lon}&appid=${apiKey}&units=imperial`;
        const r = await fetch(url);
        const d = await r.json();
        const windDeg = d.wind?.deg || 0;
        const dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
        const windDir = dirs[Math.round(windDeg / 22.5) % 16];
        return {
          team: p.team,
          city: p.city,
          dome: false,
          temp: Math.round(d.main?.temp || 0),
          feelsLike: Math.round(d.main?.feels_like || 0),
          wind: Math.round((d.wind?.speed || 0)),
          windGust: Math.round((d.wind?.gust || 0)),
          windDir,
          windDeg,
          humidity: d.main?.humidity,
          condition: d.weather?.[0]?.description || 'Unknown',
          precipChance: Math.round((d.rain?.['1h'] || 0) * 100),
          clouds: d.clouds?.all,
          icon: d.weather?.[0]?.main
        };
      } catch(e) {
        return { team: p.team, city: p.city, dome: false, error: e.message };
      }
    });

    const weatherResults = await Promise.all(weatherPromises);
    const domeResults = parks.filter(p => p.dome).map(p => ({
      team: p.team, city: p.city, dome: true,
      note: 'Dome/retractable roof — weather not a factor'
    }));

    const all = [...weatherResults, ...domeResults].sort((a,b) => a.team.localeCompare(b.team));
    const result = { updatedAt: new Date().toISOString(), parks: all };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch(error) {
    const result = { error: error.message };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(500).json(result);
  }
}
