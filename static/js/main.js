(function () {
    const body = document.body;
    const sideNav = document.getElementById("sideNav");
    const navToggle = document.getElementById("navToggle");
    const mobileNavToggle = document.getElementById("mobileNavToggle");
    const modeToggle = document.getElementById("modeToggle");
    const languageSelect = document.getElementById("languageSelect");
    const languageMessage = document.getElementById("languageMessage");
    const desktopQuery = window.matchMedia("(min-width: 992px)");

    const savedTheme = localStorage.getItem("ables-theme");
    if (savedTheme === "dark") {
        body.classList.add("dark-mode");
    }

    if (localStorage.getItem("ables-nav") === "collapsed") {
        body.classList.add("nav-collapsed");
    }

    function syncModeText() {
        if (!modeToggle) {
            return;
        }

        modeToggle.textContent = body.classList.contains("dark-mode") ? "Light Mode" : "Dark Mode";
    }

    function toggleNav() {
        if (!sideNav) {
            return;
        }

        if (desktopQuery.matches) {
            body.classList.toggle("nav-collapsed");
            localStorage.setItem(
                "ables-nav",
                body.classList.contains("nav-collapsed") ? "collapsed" : "open"
            );
            return;
        }

        sideNav.classList.toggle("is-open");
        body.classList.toggle("nav-open", sideNav.classList.contains("is-open"));
    }

    if (navToggle) {
        navToggle.setAttribute("aria-expanded", "false");
        navToggle.addEventListener("click", toggleNav);
    }

    if (mobileNavToggle) {
        mobileNavToggle.setAttribute("aria-expanded", "false");
        mobileNavToggle.addEventListener("click", toggleNav);
    }

    if (modeToggle) {
        modeToggle.setAttribute("aria-pressed", body.classList.contains("dark-mode") ? "true" : "false");
        modeToggle.addEventListener("click", function () {
            body.classList.toggle("dark-mode");
            localStorage.setItem("ables-theme", body.classList.contains("dark-mode") ? "dark" : "light");
            syncModeText();
            modeToggle.setAttribute("aria-pressed", body.classList.contains("dark-mode") ? "true" : "false");
        });
    }

    if (languageSelect) {
        const googleLanguageCodes = {
            es: "es",
            fr: "fr",
            am: "am",
            sw: "sw",
            ar: "ar",
            zh: "zh-CN",
            pt: "pt"
        };
        const languageNames = {
            en: "English",
            es: "Spanish",
            fr: "French",
            am: "Amharic",
            sw: "Kiswahili",
            kam: "Kamba",
            ar: "Arabic",
            zh: "Chinese",
            pt: "Portuguese"
        };
        const originalTextNodes = new WeakMap();
        const originalAttributes = new WeakMap();
        const blockedTranslateTags = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "IFRAME", "SVG", "CANVAS"]);

        const translations = {
            en: {
                message: "English selected. Page stays in English.",
                nav_get_care: "Get Care",
                nav_home: "Home",
                nav_family_guide: "Family Guide",
                nav_become_client: "Become a Client",
                nav_referrals: "Referrals",
                nav_provider_partners: "Provider Partners",
                nav_pricing: "Pricing & Payment",
                nav_services: "Services",
                nav_all_services: "All Services",
                nav_care_programs: "Care Programs",
                nav_care_process: "Care Process",
                nav_service_areas: "Service Areas",
                nav_trust: "Trust",
                nav_caregivers: "Caregivers",
                nav_care_standards: "Care Standards",
                nav_testimonials: "Testimonials",
                nav_resources: "Resources",
                nav_company: "Company",
                nav_about: "About Us",
                nav_why: "Why Ables",
                nav_work: "Looking for Work?",
                nav_careers: "Careers",
                nav_contact: "Contact",
                dashboard: "Dashboard",
                account: "Account",
                logout: "Logout",
                login: "Login",
                clients_label: "CLIENTS",
                looking_for_label: "LOOKING FOR?",
                client_benefits: "Great Professional Service | Compassionate Care | On Time and Reliable Service",
                benefit_professional: "Great Professional Service",
                benefit_compassionate: "Compassionate Care",
                benefit_reliable: "On Time and Reliable Service",
                click_here: "CLICK HERE",
                work_label: "LOOKING FOR WORK?",
                work_apply: "CNA, LPN, RN Apply Here",
                call_us: "CALL US",
                footer_summary: "Skilled and Non-Skilled Nursing Care Provider for clients and families in Maryland.",
                send_message: "Send a Message",
                quick_links: "Quick Links",
                brochure: "Brochure",
                privacy: "Privacy",
                terms: "Terms",
                contact_info: "Contact Information",
                language_accessibility: "Language accessibility",
                language_help: "Choose a preferred language and the page will translate automatically."
            },
            es: {
                message: "Espanol seleccionado. Traduciendo toda la pagina.",
                nav_get_care: "Recibir Cuidado",
                nav_home: "Inicio",
                nav_family_guide: "Guia Familiar",
                nav_become_client: "Ser Cliente",
                nav_referrals: "Referidos",
                nav_provider_partners: "Socios Medicos",
                nav_pricing: "Precios y Pago",
                nav_services: "Servicios",
                nav_all_services: "Todos los Servicios",
                nav_care_programs: "Programas de Cuidado",
                nav_care_process: "Proceso de Cuidado",
                nav_service_areas: "Areas de Servicio",
                nav_trust: "Confianza",
                nav_caregivers: "Cuidadores",
                nav_care_standards: "Normas de Cuidado",
                nav_testimonials: "Testimonios",
                nav_resources: "Recursos",
                nav_company: "Compania",
                nav_about: "Sobre Nosotros",
                nav_why: "Por Que Ables",
                nav_work: "Busca Trabajo?",
                nav_careers: "Empleos",
                nav_contact: "Contacto",
                dashboard: "Panel",
                account: "Cuenta",
                logout: "Cerrar Sesion",
                login: "Iniciar Sesion",
                clients_label: "CLIENTES",
                looking_for_label: "BUSCA?",
                client_benefits: "Gran Servicio Profesional | Cuidado Compasivo | Servicio Puntual y Confiable",
                benefit_professional: "Gran Servicio Profesional",
                benefit_compassionate: "Cuidado Compasivo",
                benefit_reliable: "Servicio Puntual y Confiable",
                click_here: "HAGA CLIC AQUI",
                work_label: "BUSCA TRABAJO?",
                work_apply: "CNA, LPN, o RN Solicite Aqui",
                call_us: "LLAMENOS",
                footer_summary: "Proveedor de enfermeria especializada y no especializada para clientes y familias en Maryland.",
                send_message: "Enviar Mensaje",
                quick_links: "Enlaces Rapidos",
                brochure: "Folleto",
                privacy: "Privacidad",
                terms: "Terminos",
                contact_info: "Informacion de Contacto",
                language_accessibility: "Accesibilidad de idioma",
                language_help: "Elija un idioma y la pagina se traducira automaticamente."
            },
            fr: {
                message: "Francais selectionne. Traduction de toute la page.",
                nav_get_care: "Obtenir des Soins",
                nav_home: "Accueil",
                nav_family_guide: "Guide Famille",
                nav_become_client: "Devenir Client",
                nav_referrals: "References",
                nav_provider_partners: "Partenaires",
                nav_pricing: "Prix et Paiement",
                nav_services: "Services",
                nav_all_services: "Tous les Services",
                nav_care_programs: "Programmes de Soins",
                nav_care_process: "Processus de Soins",
                nav_service_areas: "Zones Desservies",
                nav_trust: "Confiance",
                nav_caregivers: "Aidants",
                nav_care_standards: "Normes de Soins",
                nav_testimonials: "Temoignages",
                nav_resources: "Ressources",
                nav_company: "Entreprise",
                nav_about: "A Propos",
                nav_why: "Pourquoi Ables",
                nav_work: "Vous Cherchez du Travail?",
                nav_careers: "Carrieres",
                nav_contact: "Contact",
                dashboard: "Tableau de Bord",
                account: "Compte",
                logout: "Deconnexion",
                login: "Connexion",
                clients_label: "CLIENTS",
                looking_for_label: "VOUS CHERCHEZ?",
                client_benefits: "Service Professionnel | Soins Compatissants | Service Ponctuel et Fiable",
                benefit_professional: "Service Professionnel",
                benefit_compassionate: "Soins Compatissants",
                benefit_reliable: "Service Ponctuel et Fiable",
                click_here: "CLIQUEZ ICI",
                work_label: "VOUS CHERCHEZ DU TRAVAIL?",
                work_apply: "CNA, LPN, ou RN Postulez Ici",
                call_us: "APPELEZ-NOUS",
                footer_summary: "Prestataire de soins infirmiers qualifies et non qualifies pour les clients et familles du Maryland.",
                send_message: "Envoyer un Message",
                quick_links: "Liens Rapides",
                brochure: "Brochure",
                privacy: "Confidentialite",
                terms: "Conditions",
                contact_info: "Coordonnees",
                language_accessibility: "Accessibilite linguistique",
                language_help: "Choisissez une langue et la page se traduira automatiquement."
            },
            sw: {
                message: "Kiswahili kimechaguliwa. Ukurasa mzima unatafsiriwa.",
                nav_get_care: "Pata Huduma",
                nav_home: "Nyumbani",
                nav_family_guide: "Mwongozo wa Familia",
                nav_become_client: "Kuwa Mteja",
                nav_referrals: "Rufaa",
                nav_provider_partners: "Washirika wa Huduma",
                nav_pricing: "Bei na Malipo",
                nav_services: "Huduma",
                nav_all_services: "Huduma Zote",
                nav_care_programs: "Mipango ya Huduma",
                nav_care_process: "Mchakato wa Huduma",
                nav_service_areas: "Maeneo ya Huduma",
                nav_trust: "Uaminifu",
                nav_caregivers: "Wahudumu",
                nav_care_standards: "Viwango vya Huduma",
                nav_testimonials: "Ushuhuda",
                nav_resources: "Rasilimali",
                nav_company: "Kampuni",
                nav_about: "Kutuhusu",
                nav_why: "Kwa Nini Ables",
                nav_work: "Unatafuta Kazi?",
                nav_careers: "Kazi",
                nav_contact: "Mawasiliano",
                dashboard: "Dashibodi",
                account: "Akaunti",
                logout: "Toka",
                login: "Ingia",
                clients_label: "WATEJA",
                looking_for_label: "UNATAFUTA?",
                client_benefits: "Huduma Nzuri ya Kitaalamu | Huduma ya Huruma | Huduma ya Wakati na Kutegemewa",
                benefit_professional: "Huduma Nzuri ya Kitaalamu",
                benefit_compassionate: "Huduma ya Huruma",
                benefit_reliable: "Huduma ya Wakati na Kutegemewa",
                click_here: "BONYEZA HAPA",
                work_label: "UNATAFUTA KAZI?",
                work_apply: "CNA, LPN, au RN Omba Hapa",
                call_us: "TUPIGIE",
                footer_summary: "Mtoa huduma ya uuguzi wa kitaalamu na usio wa kitaalamu kwa wateja na familia Maryland.",
                send_message: "Tuma Ujumbe",
                quick_links: "Viungo vya Haraka",
                brochure: "Brosha",
                privacy: "Faragha",
                terms: "Masharti",
                contact_info: "Taarifa za Mawasiliano",
                language_accessibility: "Ufikiaji wa lugha",
                language_help: "Chagua lugha na ukurasa utatafsiriwa moja kwa moja."
            },
            kam: {
                message: "Kamba selected. Common labels are updated where translation support is available.",
                nav_get_care: "Kwithiwa na Utethyo",
                nav_home: "Mwaitu",
                nav_family_guide: "Mwongozo wa Musyi",
                nav_become_client: "Kuwa Client",
                nav_referrals: "Referrals",
                nav_provider_partners: "Provider Partners",
                nav_pricing: "Payment",
                nav_services: "Services",
                nav_all_services: "Services Syonthe",
                nav_care_programs: "Care Programs",
                nav_care_process: "Care Process",
                nav_service_areas: "Service Areas",
                nav_trust: "Trust",
                nav_caregivers: "Caregivers",
                nav_care_standards: "Care Standards",
                nav_testimonials: "Testimonials",
                nav_resources: "Resources",
                nav_company: "Company",
                nav_about: "About Us",
                nav_why: "Why Ables",
                nav_work: "Wenda Kazi?",
                nav_careers: "Careers",
                nav_contact: "Contact",
                dashboard: "Dashboard",
                account: "Account",
                logout: "Logout",
                login: "Login",
                clients_label: "CLIENTS",
                looking_for_label: "WENDA?",
                client_benefits: "Great Professional Service | Compassionate Care | On Time and Reliable Service",
                benefit_professional: "Great Professional Service",
                benefit_compassionate: "Compassionate Care",
                benefit_reliable: "On Time and Reliable Service",
                click_here: "CLICK HERE",
                work_label: "WENDA KAZI?",
                work_apply: "CNA, LPN, RN Apply Here",
                call_us: "TUTHENGE",
                footer_summary: "Skilled and Non-Skilled Nursing Care Provider kwa clients na families Maryland.",
                send_message: "Send Message",
                quick_links: "Quick Links",
                brochure: "Brochure",
                privacy: "Privacy",
                terms: "Terms",
                contact_info: "Contact Information",
                language_accessibility: "Language accessibility",
                language_help: "Select language and the website updates automatically when translation is available."
            },
            am: {
                message: "Amharic selected. Translating the full page.",
                nav_get_care: "Get Care",
                nav_home: "Home",
                nav_become_client: "Become a Client",
                looking_for_label: "LOOKING FOR?",
                client_benefits: "Great Professional Service | Compassionate Care | On Time and Reliable Service",
                benefit_professional: "Great Professional Service",
                benefit_compassionate: "Compassionate Care",
                benefit_reliable: "On Time and Reliable Service",
                click_here: "CLICK HERE",
                nav_services: "Services",
                nav_work: "Looking for Work?",
                nav_contact: "Contact",
                login: "Login",
                account: "Account",
                logout: "Logout",
                language_help: "Select language and the page translates automatically."
            },
            ar: {
                message: "Arabic selected. Translating the full page.",
                nav_get_care: "Get Care",
                nav_home: "Home",
                nav_become_client: "Become a Client",
                looking_for_label: "LOOKING FOR?",
                client_benefits: "Great Professional Service | Compassionate Care | On Time and Reliable Service",
                benefit_professional: "Great Professional Service",
                benefit_compassionate: "Compassionate Care",
                benefit_reliable: "On Time and Reliable Service",
                click_here: "CLICK HERE",
                nav_services: "Services",
                nav_work: "Looking for Work?",
                nav_contact: "Contact",
                login: "Login",
                account: "Account",
                logout: "Logout",
                language_help: "Select language and the page translates automatically."
            },
            zh: {
                message: "Chinese selected. Translating the full page.",
                nav_get_care: "获取护理",
                nav_home: "首页",
                nav_become_client: "成为客户",
                looking_for_label: "需要什么？",
                client_benefits: "专业服务 | 贴心护理 | 准时可靠服务",
                benefit_professional: "专业服务",
                benefit_compassionate: "贴心护理",
                benefit_reliable: "准时可靠服务",
                click_here: "点击这里",
                nav_services: "服务",
                nav_work: "正在找工作？",
                nav_contact: "联系",
                login: "登录",
                account: "账户",
                logout: "退出",
                language_help: "选择语言后，页面会自动翻译。"
            },
            pt: {
                message: "Portugues selecionado. Traduzindo a pagina inteira.",
                nav_get_care: "Receber Cuidado",
                nav_home: "Inicio",
                nav_become_client: "Tornar-se Cliente",
                looking_for_label: "PROCURANDO?",
                client_benefits: "Otimo Servico Profissional | Cuidado Compassivo | Servico Pontual e Confiavel",
                benefit_professional: "Otimo Servico Profissional",
                benefit_compassionate: "Cuidado Compassivo",
                benefit_reliable: "Servico Pontual e Confiavel",
                click_here: "CLIQUE AQUI",
                nav_services: "Servicos",
                nav_work: "Procurando Trabalho?",
                nav_contact: "Contato",
                login: "Entrar",
                account: "Conta",
                logout: "Sair",
                language_help: "Escolha um idioma e a pagina sera traduzida automaticamente."
            }
        };

        function setTranslateCookie(language) {
            const targetLanguage = googleLanguageCodes[language];
            const cookieValue = targetLanguage ? "/en/" + targetLanguage : "/en/en";
            const expired = "expires=Thu, 01 Jan 1970 00:00:00 GMT";

            if (!targetLanguage) {
                document.cookie = "googtrans=;path=/;" + expired;
                document.cookie = "googtrans=;domain=" + window.location.hostname + ";path=/;" + expired;
                return;
            }

            document.cookie = "googtrans=" + cookieValue + ";path=/";
            if (window.location.hostname.indexOf(".") !== -1) {
                document.cookie = "googtrans=" + cookieValue + ";domain=" + window.location.hostname + ";path=/";
            }
        }

        function triggerFullPageTranslation(language, attempt) {
            const targetLanguage = googleLanguageCodes[language];
            const combo = document.querySelector(".goog-te-combo");

            if (language === "en") {
                setTranslateCookie("en");
                return;
            }

            if (!targetLanguage) {
                return;
            }

            setTranslateCookie(language);

            if (!combo) {
                if (attempt < 20) {
                    window.setTimeout(function () {
                        triggerFullPageTranslation(language, attempt + 1);
                    }, 250);
                }
                return;
            }

            if (combo.value !== targetLanguage) {
                combo.value = targetLanguage;
                combo.dispatchEvent(new Event("change"));
            }
        }

        function shouldSkipTranslationNode(element) {
            if (!element || blockedTranslateTags.has(element.tagName)) {
                return true;
            }

            return !!element.closest(".notranslate, .translation-engine, #google_translate_element, .goog-te-banner-frame");
        }

        function rememberOriginals(root) {
            const walker = document.createTreeWalker(root || document.body, NodeFilter.SHOW_TEXT, {
                acceptNode: function (node) {
                    const parent = node.parentElement;
                    if (!parent || shouldSkipTranslationNode(parent) || !node.nodeValue.trim()) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                }
            });

            while (walker.nextNode()) {
                if (!originalTextNodes.has(walker.currentNode)) {
                    originalTextNodes.set(walker.currentNode, walker.currentNode.nodeValue);
                }
            }

            document.querySelectorAll("input, textarea, option, button, a, img").forEach(function (element) {
                if (shouldSkipTranslationNode(element)) {
                    return;
                }

                const saved = originalAttributes.get(element) || {};
                ["placeholder", "title", "aria-label", "alt"].forEach(function (attribute) {
                    if (element.hasAttribute(attribute) && !saved[attribute]) {
                        saved[attribute] = element.getAttribute(attribute);
                    }
                });

                if ((element.tagName === "INPUT" || element.tagName === "BUTTON") && element.value && !saved.value) {
                    const type = (element.getAttribute("type") || "").toLowerCase();
                    if (["button", "submit", "reset"].includes(type) || element.tagName === "BUTTON") {
                        saved.value = element.value;
                    }
                }

                if (Object.keys(saved).length) {
                    originalAttributes.set(element, saved);
                }
            });
        }

        function restoreOriginalPageText() {
            rememberOriginals(document.body);

            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
                acceptNode: function (node) {
                    const parent = node.parentElement;
                    if (!parent || shouldSkipTranslationNode(parent)) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                }
            });

            while (walker.nextNode()) {
                const original = originalTextNodes.get(walker.currentNode);
                if (original !== undefined) {
                    walker.currentNode.nodeValue = original;
                }
            }

            document.querySelectorAll("input, textarea, option, button, a, img").forEach(function (element) {
                const saved = originalAttributes.get(element);
                if (!saved) {
                    return;
                }

                Object.keys(saved).forEach(function (attribute) {
                    if (attribute === "value") {
                        element.value = saved[attribute];
                    } else {
                        element.setAttribute(attribute, saved[attribute]);
                    }
                });
            });
        }

        function getTranslationCache(language, text) {
            try {
                return sessionStorage.getItem("ables-translation-" + language + "-" + btoa(unescape(encodeURIComponent(text))).slice(0, 120));
            } catch (error) {
                return null;
            }
        }

        function setTranslationCache(language, text, translated) {
            try {
                sessionStorage.setItem(
                    "ables-translation-" + language + "-" + btoa(unescape(encodeURIComponent(text))).slice(0, 120),
                    translated
                );
            } catch (error) {
                return;
            }
        }

        async function translateText(text, language) {
            const targetLanguage = googleLanguageCodes[language];
            const trimmed = text.trim();

            if (!targetLanguage || !trimmed || /^[\d\s\-().,:/@]+$/.test(trimmed)) {
                return text;
            }

            const cached = getTranslationCache(language, trimmed);
            if (cached) {
                return text.replace(trimmed, cached);
            }

            const response = await fetch(
                "https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=" +
                    encodeURIComponent(targetLanguage) +
                    "&dt=t&q=" +
                    encodeURIComponent(trimmed)
            );
            const data = await response.json();
            const translated = Array.isArray(data[0])
                ? data[0].map(function (part) { return part[0]; }).join("")
                : trimmed;

            setTranslationCache(language, trimmed, translated);
            return text.replace(trimmed, translated);
        }

        async function translatePageDirect(language) {
            if (language === "en") {
                restoreOriginalPageText();
                return;
            }

            if (!googleLanguageCodes[language]) {
                restoreOriginalPageText();
                return;
            }

            rememberOriginals(document.body);

            const textItems = [];
            const uniqueText = new Map();
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
                acceptNode: function (node) {
                    const parent = node.parentElement;
                    if (!parent || shouldSkipTranslationNode(parent) || !node.nodeValue.trim()) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    return NodeFilter.FILTER_ACCEPT;
                }
            });

            while (walker.nextNode()) {
                const node = walker.currentNode;
                const original = originalTextNodes.get(node) || node.nodeValue;
                textItems.push({ node: node, original: original });
                if (original.trim()) {
                    uniqueText.set(original, null);
                }
            }

            const elements = Array.from(document.querySelectorAll("input, textarea, option, button, a, img"));
            const attributeItems = [];
            elements.forEach(function (element) {
                const saved = originalAttributes.get(element);
                if (!saved || shouldSkipTranslationNode(element)) {
                    return;
                }

                Object.keys(saved).forEach(function (attribute) {
                    uniqueText.set(saved[attribute], null);
                    attributeItems.push({ element: element, attribute: attribute, original: saved[attribute] });
                });
            });

            const originals = Array.from(uniqueText.keys());
            let cursor = 0;
            const workerCount = Math.min(8, originals.length);

            async function translateWorker() {
                while (cursor < originals.length) {
                    const original = originals[cursor];
                    cursor += 1;
                    try {
                        uniqueText.set(original, await translateText(original, language));
                    } catch (error) {
                        uniqueText.set(original, original);
                    }
                }
            }

            await Promise.all(Array.from({ length: workerCount }, translateWorker));

            for (let index = 0; index < textItems.length; index += 1) {
                const item = textItems[index];
                item.node.nodeValue = uniqueText.get(item.original) || item.original;
            }

            for (let index = 0; index < attributeItems.length; index += 1) {
                const item = attributeItems[index];
                const translated = uniqueText.get(item.original) || item.original;
                if (item.attribute === "value") {
                    item.element.value = translated;
                } else {
                    item.element.setAttribute(item.attribute, translated);
                }
            }

            for (let index = 0; index < elements.length; index += 1) {
                const element = elements[index];
                const saved = originalAttributes.get(element);
                if (!saved || shouldSkipTranslationNode(element)) {
                    continue;
                }

                const attributes = Object.keys(saved);
                for (let attrIndex = 0; attrIndex < attributes.length; attrIndex += 1) {
                    const attribute = attributes[attrIndex];
                    const translated = uniqueText.get(saved[attribute]) || saved[attribute];
                    if (attribute === "value") {
                        element.value = translated;
                    } else {
                        element.setAttribute(attribute, translated);
                    }
                }
            }
        }

        function applyLanguage(language) {
            const dictionary = translations[language] || translations.en;
            document.documentElement.setAttribute("lang", language === "kam" ? "en" : language);

            document.querySelectorAll("[data-i18n]").forEach(function (element) {
                const key = element.getAttribute("data-i18n");
                const text = dictionary[key] || translations.en[key];
                if (text) {
                    element.textContent = text;
                }
            });

            if (languageMessage) {
                languageMessage.textContent = dictionary.message || translations.en.message;
            }

            triggerFullPageTranslation(language, 0);
            translatePageDirect(language).then(function () {
                if (languageMessage && googleLanguageCodes[language]) {
                    languageMessage.textContent = (languageNames[language] || "Language") + " selected. The page has been translated.";
                }
            }).catch(function () {
                if (languageMessage && googleLanguageCodes[language]) {
                    languageMessage.textContent = (languageNames[language] || "Language") + " selected. Some page text may need manual translation.";
                }
            });
        }

        window.googleTranslateElementInit = function () {
            const target = document.getElementById("google_translate_element");
            if (!target || !window.google || !window.google.translate) {
                return;
            }

            new window.google.translate.TranslateElement(
                {
                    pageLanguage: "en",
                    includedLanguages: "es,fr,am,sw,ar,so,pt",
                    autoDisplay: false
                },
                "google_translate_element"
            );

            triggerFullPageTranslation(localStorage.getItem("ables-language") || "en", 0);
        };

        const savedLanguage = localStorage.getItem("ables-language") || "en";
        rememberOriginals(document.body);
        languageSelect.value = savedLanguage;
        applyLanguage(savedLanguage);

        languageSelect.addEventListener("change", function () {
            localStorage.setItem("ables-language", languageSelect.value);
            applyLanguage(languageSelect.value);
        });
    }

    document.querySelectorAll(".nav-group").forEach(function (group, index) {
        const storageKey = "ables-nav-group-" + index;
        const savedState = localStorage.getItem(storageKey);

        if (savedState === "closed") {
            group.removeAttribute("open");
        } else if (savedState === "open") {
            group.setAttribute("open", "");
        }

        group.addEventListener("toggle", function () {
            localStorage.setItem(storageKey, group.open ? "open" : "closed");
        });
    });

    document.querySelectorAll(".side-links a, .side-actions a").forEach(function (link) {
        const href = link.getAttribute("href");
        if (!href || href === "#") {
            return;
        }

        if (href === window.location.pathname || (href !== "/" && window.location.pathname.startsWith(href))) {
            link.classList.add("is-active");
            const group = link.closest("details");
            if (group) {
                group.setAttribute("open", "");
            }
        }
    });

    document.querySelectorAll("[data-table-search]").forEach(function (input) {
        const table = document.getElementById(input.getAttribute("data-table-search"));
        if (!table) {
            return;
        }

        input.addEventListener("input", function () {
            const query = input.value.trim().toLowerCase();
            table.querySelectorAll("tbody tr").forEach(function (row) {
                const text = row.textContent.toLowerCase();
                row.hidden = query.length > 0 && !text.includes(query);
            });
        });
    });

    document.querySelectorAll("[data-card-search]").forEach(function (input) {
        const list = document.getElementById(input.getAttribute("data-card-search"));
        if (!list) {
            return;
        }

        input.addEventListener("input", function () {
            const query = input.value.trim().toLowerCase();
            list.querySelectorAll("[data-search-card]").forEach(function (card) {
                const text = card.textContent.toLowerCase();
                card.hidden = query.length > 0 && !text.includes(query);
            });
        });
    });

    let draggedAppointment = null;

    document.querySelectorAll("[data-appointment-id]").forEach(function (card) {
        card.addEventListener("dragstart", function () {
            draggedAppointment = card;
            card.classList.add("is-dragging");
        });

        card.addEventListener("dragend", function () {
            card.classList.remove("is-dragging");
        });
    });

    document.querySelectorAll("[data-schedule-date]").forEach(function (dayCard) {
        dayCard.addEventListener("dragover", function (event) {
            event.preventDefault();
            dayCard.classList.add("is-drop-target");
        });

        dayCard.addEventListener("dragleave", function () {
            dayCard.classList.remove("is-drop-target");
        });

        dayCard.addEventListener("drop", function (event) {
            event.preventDefault();
            dayCard.classList.remove("is-drop-target");
            if (!draggedAppointment) {
                return;
            }

            const appointmentId = draggedAppointment.getAttribute("data-appointment-id");
            const date = dayCard.getAttribute("data-schedule-date");
            const currentTime = draggedAppointment.getAttribute("data-current-time") || "";
            const newTime = window.prompt("Move visit to this date. Enter visit time:", currentTime);
            const csrf = document.getElementById("scheduleCsrfToken");

            if (!appointmentId || !date || !newTime || !csrf) {
                return;
            }

            const formData = new FormData();
            formData.append("csrf_token", csrf.value);
            formData.append("date", date);
            formData.append("time", newTime.trim());

            fetch("/admin/appointment/" + appointmentId + "/move", {
                method: "POST",
                body: formData
            }).then(function () {
                window.location.reload();
            });
        });
    });

    function passwordScore(value) {
        const rules = {
            length: value.length >= 12,
            upper: /[A-Z]/.test(value),
            lower: /[a-z]/.test(value),
            number: /\d/.test(value),
            symbol: /[^A-Za-z0-9]/.test(value),
            common: !/(password|admin|client|caregiver|ables|homecare|123456)/i.test(value)
        };
        const passed = Object.values(rules).filter(Boolean).length;
        let label = "Weak";

        if (passed >= 6) {
            label = "Strong";
        } else if (passed >= 4) {
            label = "Good";
        } else if (passed >= 2) {
            label = "Fair";
        }

        return { label, passed, rules };
    }

    function updatePasswordHelper(helper) {
        const input = helper.querySelector("[data-password-input]");
        const status = helper.querySelector("[data-password-status]");
        const meter = helper.querySelector(".password-meter span");
        const result = passwordScore(input.value);

        helper.classList.remove("is-weak", "is-fair", "is-good", "is-strong");
        helper.classList.add("is-" + result.label.toLowerCase());

        if (meter) {
            meter.style.width = Math.max(8, (result.passed / 6) * 100) + "%";
        }

        if (status) {
            status.textContent = input.value
                ? result.label + (result.label === "Strong" ? ": safe to use." : ": add more requirements before using.")
                : "Start with at least 12 characters. Mix uppercase, lowercase, number, and symbol.";
        }

        Object.entries(result.rules).forEach(function ([rule, passed]) {
            const item = helper.querySelector('[data-rule="' + rule + '"]');
            if (item) {
                item.classList.toggle("is-met", passed);
            }
        });
    }

    document.querySelectorAll("[data-password-helper]").forEach(function (helper) {
        const input = helper.querySelector("[data-password-input]");
        if (!input) {
            return;
        }

        updatePasswordHelper(helper);
        input.addEventListener("input", function () {
            updatePasswordHelper(helper);
            document.querySelectorAll("[data-password-match]").forEach(syncPasswordMatch);
        });
    });

    function syncPasswordMatch(status) {
        const source = document.getElementById(status.getAttribute("data-password-match"));
        const confirm = status.previousElementSibling;
        if (!source || !confirm || !confirm.value) {
            status.textContent = "Passwords must match.";
            status.classList.remove("is-safe", "is-danger");
            return;
        }

        const matches = source.value === confirm.value;
        status.textContent = matches ? "Passwords match." : "Passwords do not match yet.";
        status.classList.toggle("is-safe", matches);
        status.classList.toggle("is-danger", !matches);
    }

    document.querySelectorAll("[data-password-match]").forEach(function (status) {
        syncPasswordMatch(status);
        const confirm = status.previousElementSibling;
        if (confirm) {
            confirm.addEventListener("input", function () {
                syncPasswordMatch(status);
            });
        }
    });

    document.querySelectorAll("[data-password-toggle]").forEach(function (button) {
        button.addEventListener("click", function () {
            const input = document.getElementById(button.getAttribute("data-password-toggle"));
            if (!input) {
                return;
            }

            const shouldShow = input.type === "password";
            input.type = shouldShow ? "text" : "password";
            button.textContent = shouldShow ? "Hide" : "Show";
        });
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && sideNav && sideNav.classList.contains("is-open")) {
            sideNav.classList.remove("is-open");
            body.classList.remove("nav-open");
        }
    });

    desktopQuery.addEventListener("change", function () {
        sideNav.classList.remove("is-open");
        body.classList.remove("nav-open");
    });

    syncModeText();
})();

