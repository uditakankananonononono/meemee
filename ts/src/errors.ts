export class MeemeeError extends Error { constructor(message: string) { super(message); this.name = new.target.name; } }
export class ApiError extends MeemeeError {
  constructor(message: string, public readonly statusCode: number, public readonly requestId?: string, public readonly detail?: unknown) { super(message); }
}
export class BadRequestError extends ApiError {}
export class AuthenticationError extends ApiError { constructor(message: string, statusCode=401, requestId?: string, detail?: unknown, public readonly wwwAuthenticate?: string) { super(message,statusCode,requestId,detail); } }
export class PermissionDeniedError extends ApiError { constructor(message: string,statusCode=403,requestId?: string,detail?: unknown,public readonly missingScope?: string){super(message,statusCode,requestId,detail)} }
export class NotFoundError extends ApiError {}
export class ConflictError extends ApiError {}
export class IdempotencyConflictError extends ConflictError { constructor(message:string,statusCode=409,requestId?:string,detail?:unknown,public readonly idempotencyKey?:string){super(message,statusCode,requestId,detail)} }
export class ValidationError extends ApiError { constructor(message:string,statusCode=422,requestId?:string,detail?:unknown,public readonly issues?: Record<string,unknown>[]){super(message,statusCode,requestId,detail)} }
export class RateLimitError extends ApiError { constructor(message:string,statusCode=429,requestId?:string,detail?:unknown,public readonly retryAfter?:number){super(message,statusCode,requestId,detail)} }
export class ServerError extends ApiError {}
export class NetworkError extends MeemeeError { constructor(message:string,public readonly cause?:unknown){super(message)} }
export class StreamError extends MeemeeError {}
export class WaitTimeoutError extends MeemeeError {}
