import type {Lang} from './types';
import {errorText} from './i18n';
export type BotPlatform='telegram'|'discord';
export type ManagedBot={id:string;name:string;platform:BotPlatform;image:string;desired_state:'running'|'stopped';observed_state:'running'|'stopped'|'error';revision:number;observed_revision:number;last_error:string;last_logs:string;last_checked:string|null};
export type BotGuard={owner:boolean;mfa_enabled:boolean;unlocked:boolean;runner_ready:boolean;access_expires_at:string|null;server_time:string;limits:{max_bots:number;memory_mb:number;cpus:number}};
export type BotStatus='running'|'stopped'|'starting'|'stopping'|'updating'|'error'|'waiting'|'stale';
export type StatusFilter='all'|'running'|'attention';
export function botStatus(bot:ManagedBot,now=Date.now()):BotStatus{
 if(!bot.last_checked)return bot.desired_state==='running'?'starting':'waiting';
 const checked=Date.parse(bot.last_checked);
 if(!Number.isFinite(checked)||now-checked>90000)return 'stale';
 if(bot.observed_revision!==bot.revision)return bot.desired_state==='stopped'?'stopping':bot.observed_state==='running'?'updating':'starting';
 if(bot.observed_state==='error')return 'error';
 if(bot.observed_state!==bot.desired_state)return bot.desired_state==='running'?'starting':'stopping';
 return bot.observed_state;
}
export const statusLabels:Record<BotStatus,[string,string]>={running:['Работает','Running'],stopped:['Остановлен','Stopped'],starting:['Запускается','Starting'],stopping:['Останавливается','Stopping'],updating:['Обновляется','Updating'],error:['Ошибка запуска','Runtime error'],waiting:['Ждёт проверки','Awaiting runner'],stale:['Нет свежих данных','Status out of date']};
export function filterBots(bots:ManagedBot[],query:string,platform:BotPlatform|'all',status:StatusFilter,now=Date.now()){
 const search=query.trim().toLocaleLowerCase();
 return bots.filter(bot=>{const state=botStatus(bot,now);return (!search||bot.name.toLocaleLowerCase().includes(search)||bot.image.toLocaleLowerCase().includes(search))&&(platform==='all'||bot.platform===platform)&&(status==='all'||(status==='running'?state==='running':['error','stale'].includes(state)));});
}
const errors:Record<string,[string,string]>={
 invalid_bot_token:['Проверьте токен выбранной платформы. Вставьте только токен, без кавычек и префикса Bot.','Check the platform token. Paste only the token, without quotes or a Bot prefix.'],
 bot_platform_token_required:['При смене платформы нужен новый токен.','A new token is required when switching platforms.'],
 bot_image_must_be_owned_pinned_ghcr_digest:['Укажите образ ghcr.io/saitwors/имя@sha256: и 64 символа digest. Тег latest не подходит.','Use ghcr.io/saitwors/name@sha256: followed by the 64-character digest. Version tags are not supported.'],
 bot_limit_reached:['Все три места заняты. Удалите ненужного бота, чтобы добавить нового.','All three slots are occupied. Remove a bot to add another.'],
 bot_mfa_step_up_required:['Доступ истёк. Введите свежий код 2FA, чтобы продолжить.','Access expired. Enter a fresh 2FA code to continue.'],
 bot_mfa_required:['Включите двухфакторную аутентификацию в настройках аккаунта.','Enable two-factor authentication in account settings.'],
 bot_owner_only:['Управление ботами доступно только первоначальному владельцу Campus Flow.','Only the original Campus Flow owner can manage bots.'],
 bot_runner_not_configured:['Runner ещё не настроен на сервере. Подключите его по инструкции.','The host runner is not configured. Follow the setup guide.'],
 validation:['Проверьте поля: название, полный digest образа и токен выбранной платформы.','Check the fields: name, complete image digest and platform token.'],
};
export function botError(code:string,lang:Lang){return errors[code]?.[lang==='ru'?0:1]||errorText(lang,{code});}
