const revealItems = document.querySelectorAll("[data-reveal]");
const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      }
    });
  },
  { threshold: 0.12 }
);

revealItems.forEach((item) => observer.observe(item));

const bookingForm = document.getElementById("bookingForm");
if (bookingForm) {
  const serviceSelect = document.getElementById("service_id");
  const dateInput = document.getElementById("appointment_date");
  const slotGrid = document.getElementById("slotGrid");
  const timeInput = document.getElementById("appointment_time");

  const renderSlots = (slots) => {
    slotGrid.innerHTML = "";
    if (!slots.length) {
      slotGrid.innerHTML = '<span class="slot-message">Aucun créneau disponible pour cette date.</span>';
      timeInput.value = "";
      return;
    }

    const selected = slotGrid.dataset.selected || timeInput.value;
    slots.forEach((slot) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "slot-button";
      button.textContent = slot;
      if (slot === selected) {
        button.classList.add("active");
        timeInput.value = slot;
      }
      button.addEventListener("click", () => {
        document.querySelectorAll(".slot-button").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        timeInput.value = slot;
        slotGrid.dataset.selected = slot;
      });
      slotGrid.appendChild(button);
    });
  };

  const loadSlots = async () => {
    const serviceId = serviceSelect.value;
    const date = dateInput.value;
    if (!serviceId || !date) {
      slotGrid.innerHTML = '<span class="slot-message">Sélectionnez une prestation et une date.</span>';
      return;
    }

    slotGrid.innerHTML = '<span class="slot-message">Chargement des créneaux...</span>';
    try {
      const response = await fetch(`/api/slots?service_id=${encodeURIComponent(serviceId)}&date=${encodeURIComponent(date)}`);
      const data = await response.json();
      renderSlots(data.slots || []);
    } catch (_error) {
      slotGrid.innerHTML = '<span class="slot-message">Impossible de charger les créneaux.</span>';
    }
  };

  serviceSelect.addEventListener("change", () => {
    timeInput.value = "";
    slotGrid.dataset.selected = "";
    loadSlots();
  });
  dateInput.addEventListener("change", () => {
    timeInput.value = "";
    slotGrid.dataset.selected = "";
    loadSlots();
  });
  loadSlots();
}
