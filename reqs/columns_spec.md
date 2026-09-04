`GetFullReportResponse`.

## **Общая информация**

| Поле (спецификация) | Значение |
| ----- | ----- |
| `reportDate` | Дата формирования отчёта |
| `baseInfo.inn` | ИНН организации |
| `baseInfo.ogrn` | ОГРН организации |
| `baseInfo.shortName` | Краткое наименование контрагента |
| `baseInfo.fullName` | Полное наименование контрагента |
| `baseInfo.riskLevel` | Уровень риска контрагента: `LOW` / `MEDIUM` / `HIGH` |
| `baseInfo.kpp` | КПП контрагента |
| `baseInfo.okpo` | ОКПО контрагента |
| `baseInfo.address` | Юридический адрес |
| `baseInfo.email` | E-mail компании |
| `baseInfo.website` | Адрес сайта компании |
| `baseInfo.companySize` | Описание размера организации (например, «Микропредприятие») |
| `baseInfo.staff` | Диапазон численности персонала |
| `baseInfo.registrationInfo.registrationDate` | Дата регистрации организации |
| `baseInfo.registrationInfo.yearsFromRegistration` | Сколько лет существует компания |
| `phones[].phoneType` | Тип телефона |
| `phones[].phoneCode` | Код телефона |
| `phones[].phoneNumber` | Номер телефона |
| `status.status` | Статус организации: `CURRENT` (действующая) / `CLOSED` (ликвидированная) |
| `status.reasonName` | Причина закрытия организации |
| `status.date` | Дата последнего обновления статуса |
| `zskRiskLevel` | Уровень риска «Знай своего клиента»: `GREEN` / `YELLOW` / `RED (в интерфейс выводится Green/grey/grey)` |

## **Учредители, руководство, структура**

| Поле (спецификация) | Значение |
| ----- | ----- |
| `foundersInfo.shareCapital` | Уставной капитал |
| `foundersInfo.cofounders[].name` | ФИО учредителя |
| `foundersInfo.cofounders[].inn` | ИНН учредителя |
| `foundersInfo.cofounders[].amount` | Сумма доли учредителя в капитале |
| `foundersInfo.cofounders[].share` | Доля учредителя в процентах |
| `foundersInfo.cofounders[].dateFrom` | Дата вхождения в состав учредителей |
| `foundersInfo.cofounders[].isActive` | Признак, является ли учредитель активным |
| `foundersInfo.authPerson.name` | ФИО руководителя организации |
| `foundersInfo.authPerson.positionName` | Должность руководителя |
| `foundersInfo.authPerson.inn` | ИНН руководителя |
| `foundersInfo.authPerson.positionDate` | Дата вступления в должность |
| `foundersInfo.parentOrganizations[].inn` | ИНН управляющей компании |
| `foundersInfo.parentOrganizations[].ogrn` | ОГРН управляющей компании |
| `foundersInfo.parentOrganizations[].fullName` | Полное наименование управляющей компании |
| `foundersInfo.parentOrganizations[].parentDate` | Дата начала управления |
| `relatedCompanies[].inn` | ИНН связанной организации |
| `relatedCompanies[].ogrn` | ОГРН связанной организации |
| `relatedCompanies[].name` | Краткое наименование связанной организации |
| `relatedCompanies[].registrationDate` | Дата регистрации связанной организации |
| `relatedCompanies[].authPersonName` | ФИО руководителя связанной организации |
| `relatedCompanies[].authPersonPosition` | Должность руководителя связанной организации |
| `relatedCompanies[].parentOrganizations[]` | Управляющие организации для связанной организации |
| `kindsOfActivityInfo.mainKindOfActivity.code` | Код ОКВЭД основного вида деятельности |
| `kindsOfActivityInfo.mainKindOfActivity.description` | Название основного вида деятельности |
| `kindsOfActivityInfo.otherKindsOfActivity[].code` | Код ОКВЭД дополнительного вида деятельности |
| `kindsOfActivityInfo.otherKindsOfActivity[].description` | Название дополнительного вида деятельности |
| `branchesInfo.branchesCount` | Количество филиалов |
| `branchesInfo.branches[].name` | Наименование филиала |
| `branchesInfo.branches[].address` | Юридический адрес филиала |
| `taxSystem[].fullName` | Полное название режима налогообложения |
| `taxSystem[].shortName` | Краткое название режима налогообложения |

## **Юридические риски**

| Поле (спецификация) | Значение |
| ----- | ----- |
| `arbitrationCases[].year` | Год |
| `arbitrationCases[].plaintiffCount` | Количество дел, где контрагент — истец |
| `arbitrationCases[].plaintiffAmount` | Сумма по делам, где контрагент — истец |
| `arbitrationCases[].defendantCount` | Количество дел, где контрагент — ответчик |
| `arbitrationCases[].defendantAmount` | Сумма по делам, где контрагент — ответчик |
| `arbitrationByStatus.commonCount` | Общее количество арбитражных дел |
| `arbitrationByStatus.commonAmount` | Общая сумма по арбитражным делам |
| `arbitrationByStatus.plaintiffArbitration.plaintiffArbitrationFinished.pfCount` | Кол-во закрытых дел в качестве истца |
| `...pfAmount` | Сумма по закрытым делам в качестве истца |
| `...plaintiffArbitrationAppealed.paCount` | Кол-во обжалованных дел в качестве истца |
| `...paAmount` | Сумма по обжалованным делам в качестве истца |
| `...plaintiffArbitrationPending.ppCount` | Кол-во открытых дел в качестве истца |
| `...ppAmount` | Сумма по открытым делам в качестве истца |
| `arbitrationByStatus.defandantArbitration.defandantArbitrationFinished.dfCount` | Кол-во закрытых дел в качестве ответчика |
| `...dfAmount` | Сумма по закрытым делам в качестве ответчика |
| `...defandantArbitrationAppealed.daCount` | Кол-во обжалованных дел в качестве ответчика |
| `...daAmount` | Сумма по обжалованным делам в качестве ответчика |
| `...defandantArbitrationPending.dpCount` | Кол-во открытых дел в качестве ответчика |
| `...dpAmount` | Сумма по открытым делам в качестве ответчика |
| `executionProceedings[].active` | Признак активности исполнительного производства |
| `executionProceedings[].number` | Номер исполнительного производства |
| `executionProceedings[].date` | Дата исполнительного производства |
| `executionProceedings[].amount` | Сумма исполнительного производства |
| `inspections[].erpId` | Идентификатор проверки |
| `inspections[].type` | Тип проверки |
| `inspections[].form` | Форма проверки |
| `inspections[].authorityName` | Наименование контролирующего органа |
| `inspections[].startDate` | Дата начала проверки |
| `inspections[].endDate` | Дата окончания проверки |
| `inspections[].inspectionStatus` | Статус проверки (предстоящая / завершена без нарушений / завершена с нарушениями / результат неизвестен / отменена) |
| `licenses[].number` | Номер лицензии |
| `licenses[].name` | Название лицензии |
| `licenses[].issuingAuthority` | Орган, выдавший лицензию |
| `licenses[].issueDate` | Дата выдачи лицензии |
| `licenses[].endDate` | Дата окончания действия лицензии |
| `licenses[].status` | Статус лицензии: `ACTIVE` / `EXPIRED` / `INDEFINITE` |

## **Репутационные риски**

| Поле (спецификация) | Значение |
| ----- | ----- |
| `reputationalRisks.negative[].code` | Код негативного репутационного фактора |
| `reputationalRisks.negative[].name` | Описание негативного репутационного фактора |
| `reputationalRisks.negative[].chapter` | Раздел, к которому относится фактор |
| `reputationalRisks.positive[].code` | Код позитивного репутационного фактора |
| `reputationalRisks.positive[].name` | Описание позитивного репутационного фактора |
| `reputationalRisks.positive[].chapter` | Раздел, к которому относится фактор |

## **Финансовые показатели**

| Поле (спецификация) | Значение |
| ----- | ----- |
| `finReports[].common.year` | Год |
| `finReports[].common.proceeds` | Выручка |
| `finReports[].common.profit` | Прибыль (убыток) |
| `finReports[].assets.totalAssets` | Общая сумма всех активов |
| `finReports[].assets.currentAssets.total` | Общая сумма оборотных активов |
| `finReports[].assets.currentAssets.stocks` | Запасы |
| `finReports[].assets.currentAssets.receivables` | Дебиторская задолженность |
| `finReports[].assets.currentAssets.bankroll` | Денежные средства и эквиваленты |
| `finReports[].assets.uncurrentAssets.total` | Общая сумма внеоборотных активов |
| `finReports[].assets.uncurrentAssets.fixedAssets` | Основные средства |
| `finReports[].liabilities.totalLiabilities` | Всего пассивов |
| `finReports[].liabilities.capitals` | Капиталы и резервы |
| `finReports[].liabilities.longTermDuties.total` | Общая сумма долгосрочных обязательств |
| `finReports[].liabilities.longTermDuties.others` | Прочие долгосрочные обязательства |
| `finReports[].liabilities.shortTermLiabilities.total` | Общая сумма краткосрочных обязательств |
| `finReports[].liabilities.shortTermLiabilities.borrowedFunds` | Краткосрочно-заёмные средства |
| `finReports[].liabilities.shortTermLiabilities.accountsPayable` | Кредиторская задолженность |
| `coefficient.year` | Год расчёта коэффициентов |
| `coefficient.sustainability` | Коэффициент финансовой устойчивости |
| `coefficient.solvency` | Коэффициент платёжеспособности |
| `coefficient.profitability` | Коэффициент рентабельности |

## **Госзакупки**

| Поле (спецификация) | Значение |
| ----- | ----- |
| `procurements[].tenderAdmittedCnt` | Количество тендеров, в которых контрагент принимал участие |
| `procurements[].tenderWinnerCnt` | Количество тендеров, в которых контрагент выиграл |
| `procurements[].contractSignedCnt` | Количество подписанных контрактов |
| `procurements[].contractSignedAmt` | Сумма подписанных контрактов |
| `procurements[].federalLawCode` | Код федерального закона |
| `procurements[].procurementsYear` | Год проведения госзакупки |

---

