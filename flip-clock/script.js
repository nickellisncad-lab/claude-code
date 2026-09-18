const FLIP_DURATION = 640; // ms, must match CSS (0.32s + 0.32s)

function createDigitCard(initialValue) {
  const root = document.createElement('div');
  root.className = 'flip-digit';

  const topHalf = document.createElement('div');
  topHalf.className = 'flip-digit__half top';
  const topText = document.createElement('div');
  topText.className = 'digit-text';
  topText.textContent = initialValue;
  topHalf.appendChild(topText);

  const bottomHalf = document.createElement('div');
  bottomHalf.className = 'flip-digit__half bottom';
  const bottomText = document.createElement('div');
  bottomText.className = 'digit-text';
  bottomText.textContent = initialValue;
  bottomHalf.appendChild(bottomText);

  root.appendChild(topHalf);
  root.appendChild(bottomHalf);

  let current = initialValue;
  let animating = false;

  function update(newValue) {
    if (newValue === current || animating) {
      current = newValue;
      topText.textContent = newValue;
      bottomText.textContent = newValue;
      return;
    }

    animating = true;
    const previousValue = current;

    const leafTop = document.createElement('div');
    leafTop.className = 'flip-leaf leaf-top';
    const leafTopText = document.createElement('div');
    leafTopText.className = 'digit-text';
    leafTopText.textContent = previousValue;
    leafTop.appendChild(leafTopText);

    const leafBottom = document.createElement('div');
    leafBottom.className = 'flip-leaf leaf-bottom';
    const leafBottomText = document.createElement('div');
    leafBottomText.className = 'digit-text';
    leafBottomText.textContent = newValue;
    leafBottom.appendChild(leafBottomText);

    // reveal the new value under the top leaf immediately so that as it
    // rotates away it uncovers the correct digit
    topText.textContent = newValue;

    root.appendChild(leafTop);
    root.appendChild(leafBottom);

    setTimeout(() => {
      bottomText.textContent = newValue;
      leafTop.remove();
      leafBottom.remove();
      current = newValue;
      animating = false;
    }, FLIP_DURATION);
  }

  return { root, update };
}

function createUnit(containerId, digitCount) {
  const container = document.getElementById(containerId);
  const group = document.createElement('div');
  group.className = 'card-group';

  const cards = [];
  for (let i = 0; i < digitCount; i++) {
    const card = createDigitCard('0');
    cards.push(card);
    group.appendChild(card.root);
  }

  container.insertBefore(group, container.firstChild);

  return {
    setValue(numberString) {
      const padded = numberString.padStart(digitCount, '0');
      for (let i = 0; i < digitCount; i++) {
        cards[i].update(padded[i]);
      }
    }
  };
}

const hoursUnit = createUnit('unit-hours', 2);
const minutesUnit = createUnit('unit-minutes', 2);
const secondsUnit = createUnit('unit-seconds', 2);

const dateEl = document.getElementById('date');
const dateFormatter = new Intl.DateTimeFormat(undefined, {
  weekday: 'long',
  year: 'numeric',
  month: 'long',
  day: 'numeric'
});

let lastRendered = null;

function tick() {
  const now = new Date();
  const h = String(now.getHours()).padStart(2, '0');
  const m = String(now.getMinutes()).padStart(2, '0');
  const s = String(now.getSeconds()).padStart(2, '0');
  const key = h + m + s;

  if (key !== lastRendered) {
    hoursUnit.setValue(h);
    minutesUnit.setValue(m);
    secondsUnit.setValue(s);
    lastRendered = key;
  }

  dateEl.textContent = dateFormatter.format(now);
}

tick();
setInterval(tick, 200);

// theme toggle with persistence
const themeToggle = document.getElementById('themeToggle');
const root = document.documentElement;

function applyTheme(theme) {
  if (theme === 'light') {
    root.setAttribute('data-theme', 'light');
    themeToggle.textContent = '☀️';
  } else {
    root.removeAttribute('data-theme');
    themeToggle.textContent = '🌙';
  }
}

let savedTheme = null;
try {
  savedTheme = localStorage.getItem('flip-clock-theme');
} catch (e) {
  savedTheme = null;
}
applyTheme(savedTheme || 'dark');

themeToggle.addEventListener('click', () => {
  const isLight = root.getAttribute('data-theme') === 'light';
  const next = isLight ? 'dark' : 'light';
  applyTheme(next);
  try {
    localStorage.setItem('flip-clock-theme', next);
  } catch (e) {
    /* ignore */
  }
});
